#!/usr/bin/env python3
# coding=utf-8
# Billon 2021

# QUICK README
"""
Script for publishing public and private documents with random content.
Main parameters (see Config):
    documentsToPublish - number of documents to publish per publisher
    sizeKB - size of document
    max_queue_size - max number of publications in progress
    min_queue_size - min number of publications in progress
    identitiesFilename - file with identities to be used for private documents
Script generates raport and writes it to the file

Usage examples:
    1) ./DurableMediaTest.py --publishers "{'10.0.20.140': ['31404']}" setup
    2) ./DurableMediaTest.py --publishers "{'10.0.20.140': ['31404']}" categories
    3) [public]./DurableMediaTest.py --publishers "{'10.0.20.140': ['31404']}" run
       [private] ./DurableMediaTest.py --publishers "{'10.0.20.140': ['31404']}" run  --private

Examples of publisher lists:
    --publishers "{'10.0.20.140': ['31404']}"
    --publishers "{'10.0.20.140': ['31404', '12345', '3456']}"
    --publishers "{'10.0.20.140': ['31404'], '10.0.20.141': ['31404'], '10.0.20.142': ['31404', '56789']}"

needed files:
    DurableMediaTest.py
    DurableMediaTestConfig.py
    publishers.csv (only for private documents) {two columns with pubID, pubCIF}
    soap folder with wsdls
"""

try:
    from OnChainDocumentsDurableMediaTestConfig import Config
    from soap import SoapAPI
except ImportError:
    from colony_scripts.colony.tests.DurableMediaTestConfig import Config
    from colony_scripts.colony.tests.soap import SoapAPI

import os
import time
import concurrent.futures
import threading
import hashlib
import logging
import traceback
import datetime
import csv
import itertools
import multiprocessing.synchronize
import random
import signal
import sys
from collections import defaultdict

logger = logging.getLogger("DurableMediaTest")

global_state = None


def signal_handler(sig, frame):
    logger.warning('Exiting, signal {} called'.format(sig))
    global_state.exit.set()


class PrivateDocsPublishingManager:

    def getEndpoint(self, ip, port):
        endpoint = SoapAPI.PublisherEndpoint(ip, port)
        return endpoint

    def getPublisherId(self, endpoint):
        ans = endpoint.Hello()
        return ans.publisherId

    def mapPubList(self, conf):
        publishersWithCif = dict()
        identityDict = self.getIdentities(conf.identitiesFilename)
        if identityDict is None:
            return None
        for ip, ports in conf.pubs.items():
            for port in ports:
                endpoint = self.getEndpoint(ip, port)
                try:
                    pubId = self.getPublisherId(endpoint)
                except ConnectionError as e:
                    logger.error('Unable to get publisherId for endpoint! ' + str(ip) + ' ' + str(port))
                    return None
                soap_address = 'http://' + ip + ':' + str(port)
                if pubId in identityDict:
                    publishersWithCif[soap_address] = identityDict[pubId]
                    logger.debug('For this pubId: ' + str(pubId) + ' got cif list: ' + str(identityDict[pubId]))
                else:
                    logger.error('I do not have this pubId in my identity list! ' + str(pubId))
        return publishersWithCif

    def getIdentities(self, identitiesFilename):
        """ Read from given csv file where two first columns hold publisherCif and publisherId:
        publisherID, publishercif1,
        publisherID, publisherCif2 etc..
        returns dictionary like given below:
        {'publisherID': ['publisherCif1', 'publisherCif2'], 'publisherID2': ['publisherCif3']} """
        identityDict = defaultdict(list)
        with open(identitiesFilename, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                identityDict[row[0]].append(row[1])
        if len(identityDict) == 0:
            logger.error('No identities read from csv!')
            return None
        return identityDict


class GlobalState:
    def __init__(self):
        self.exit = multiprocessing.Event()
        self.lock = multiprocessing.Lock()


class LoopStats:
    def __init__(self):
        self.added_ready = 0
        self.stats_checked_ready = 0
        self.stats_checked_in_progress = 0
        self.stats_checked_not_active = 0


class PublicationSlot:
    def __init__(self, publisher, status):
        self.publisher = publisher
        self.status = status
        self.start = None
        self.jobId = None
        self.startBrgTime = None
        self.hashContent = None
        self.blockchainAddress = None
        self.taskId = None

    def resetToReadyToPublish(self):
        self.status = 'READY_TO_PUBLISH'
        self.start = None
        self.jobId = None
        self.startBrgTime = None
        self.hashContent = None
        self.blockchainAddress = None
        self.taskId = None

    def setNotActive(self):
        self.status = 'NOT_ACTIVE'

    def isActive(self):
        return self.status != 'NOT_ACTIVE'


class SinglePublisherResult:
    def __init__(self):
        self.mean_duration = 0
        self.publications = []
        self.publishedOk = 0
        self.publishedFail = 0


class MutatingPublisherState:
    def __init__(self):
        self.publisherLock = threading.Lock()

        self.index = 0
        self.localPublishedFail = 0
        self.localPublishedOk = 0
        self.active = 0
        self.max_active = 0

    def incGetIndex(self):
        with self.publisherLock:
            self.index += 1
            return self.index

    def incLocalPublishedFail(self):
        with self.publisherLock:
            self.localPublishedFail += 1

    def incLocalPublishedOk(self):
        with self.publisherLock:
            self.localPublishedOk += 1


class SinglePublisherState:
    def __init__(self, conf, url, to_publish, private=False):
        self.conf = conf
        self.result = SinglePublisherResult()
        self.to_publish = to_publish
        self.binaries = {}
        self.url = url
        self.private = private
        self.mut: MutatingPublisherState = None
        self.cif = None

    def initSharedState(self):
        self.mut = MutatingPublisherState()

    def getEndpoint(self):
        pubPort = self.url.split(":")[-1]
        pubIp = self.url.split(":")[1].strip('/')
        return SoapAPI.PublisherEndpoint(pubIp, pubPort)

    def addToReport(
            self,
            start,
            jobId,
            blockchainAddress,
            contentHash,
            status,
            url,
            max_active,
            start_brg_time,
            create_brg_time,
            published_brg_time,
            cif='no_cif'):
        pubTime = round(self.conf.getTime() - start, self.conf.ACC)
        pubEndTime = self.conf.getTime()

        if blockchainAddress is None:
            blockchainAddress = '------------------------------------------------'
        if jobId is None:
            jobId = '------------------------'
        logger.debug('addToReport status:' + status)
        if status == "FINISHED_OK":
            success = True
            duration_brg_time = published_brg_time - start_brg_time
        else:
            success = False
            duration_brg_time = .0
            create_brg_time = .0
            published_brg_time = .0
        if not start_brg_time:
            start_brg_time = .0

        pub = {
            'doc_hash': blockchainAddress,
            'md5': contentHash,
            'pub_task_id': jobId,
            'pub_address': url,
            'cif': cif,
            'dur_time': pubTime,
            'status': status,
            'pub_end_time': pubEndTime,
            'threads': max_active,
            'start_brg_time': start_brg_time,
            'create_brg_time': create_brg_time,
            'published_brg_time': published_brg_time,
            'dur_brg_time': duration_brg_time,
        }

        logger.info("{} {} {} {} {} {:3.3f} {:<11} {} {:3.3f} {}".format(blockchainAddress, jobId, contentHash, url, cif, pubTime, status, pubEndTime, duration_brg_time, max_active))
        with self.mut.publisherLock:
            self.result.publications.append(pub)
            if success:
                self.result.publishedOk += 1
                # https://math.stackexchange.com/a/106720
                self.result.mean_duration = self.result.mean_duration + ((duration_brg_time - self.result.mean_duration) / self.result.publishedOk)
            else:
                self.result.publishedFail += 1

    def reserveDocumentsToPublish(self, to_reserve):
        to_publish_before = self.to_publish
        max_to_publish = min(self.to_publish, to_reserve)
        self.to_publish -= max_to_publish

        logger.debug('Want to reserve: {} from: {}, reserved: {}'.format(to_reserve, to_publish_before, max_to_publish))

        return max_to_publish

    def getRandomContent(self, sizeKB, suffix):
        # lock?
        if sizeKB not in self.binaries:
            self.binaries[sizeKB] = os.urandom(1024 * sizeKB)
        result = str.encode("%PDF-1.1") + str.encode(suffix) + self.binaries[sizeKB]
        return result

    def calculateQueue(self, minimum, maximum):
        max_active_now = self.mut.max_active
        if self.mut.localPublishedFail >= self.mut.localPublishedOk:
            if int(max_active_now / 2) < minimum:
                max_active_now = minimum
            else:
                max_active_now = int(max_active_now / 2)
        elif 8 * self.mut.localPublishedFail >= self.mut.localPublishedOk:
            if int(max_active_now / 1.5 < minimum):
                max_active_now = minimum
            else:
                max_active_now = int(max_active_now / 1.5)
        elif self.mut.localPublishedFail > 0:
            if int(max_active_now / 1.1 < minimum):
                max_active_now = minimum
            else:
                max_active_now = int(max_active_now / 1.1)
        else:
            if max(int(max_active_now * 1.2), max_active_now + 1) > maximum:
                max_active_now = maximum
            else:
                max_active_now = max(int(max_active_now * 1.2), max_active_now + 1)
        return max_active_now

    def handleReadySlot(self, pub: PublicationSlot, loop_stats: LoopStats):
        with self.mut.publisherLock:
            if self.mut.active <= self.mut.max_active and self.reserveDocumentsToPublish(1) == 1:
                loop_stats.added_ready += 1
                pub.resetToReadyToPublish()
                return True
            pub.setNotActive()
            self.mut.active -= 1
            return False

    def processPublication(self, pub, loop_stats):
        try:
            repeatPub = True
            while repeatPub:
                repeatPub = False
                logger.debug("pubStatus: " + pub.status)
                # Did not start publishing yet
                if pub.status == 'READY_TO_PUBLISH':
                    loop_stats.stats_checked_ready += 1
                    start = self.conf.getTime()
                    index = self.mut.incGetIndex()
                    content = self.getRandomContent(int(self.conf.sizeKB), str(index) + 'A' + self.url)
                    creationDate = str(int(start) * 10**6)
                    hashContent = Utils.md5(content)
                    randomText = 'RandomText_' + str(random.randint(1000000, 9999999))
                    try:
                        sendStartTime = self.conf.getTime()
                        retentionDate = Utils.getRandomRetention()
                        if self.private:

                            retPublish = self.getEndpoint().PublishPrivateDocument({
                                'publisherCif': self.cif,
                                'publicationMode': 'NEW',
                                'documentData': {
                                    'title': randomText,
                                    'sourceDocument': content,
                                    'documentMainCategory': 'ROOT',
                                    'documentSystemCategory': 'ROOT',
                                    'BLOCKCHAINlegalValidityStartDate': creationDate,
                                    'BLOCKCHAINexpirationDate': retentionDate,
                                    'BLOCKCHAINretentionDate': retentionDate,
                                    'extension': 'PDF',
                                    'additionalDetails': 'Here be PUBLIC additional details',
                                    'privateAdditionalDetails': 'Here be PRIVATE additional details',
                                },
                                'sendAuthorizationCodes': 'true',
                            })
                        else:
                            retPublish = self.getEndpoint().PublishPublicDocument({
                                'publicationMode': 'NEW',
                                'documentData': {
                                    'title': randomText,
                                    'sourceDocument': content,
                                    'documentMainCategory': 'ROOT',
                                    'documentSystemCategory': 'ROOT',
                                    'BLOCKCHAINlegalValidityStartDate': creationDate,
                                    'BLOCKCHAINexpirationDate': retentionDate,
                                    'BLOCKCHAINretentionDate': retentionDate,
                                    'extension': 'PDF',
                                }
                            })
                        if self.conf.debug9000:
                            logger.debug('response: ' + str(retPublish))
                        try:
                            status = retPublish.status.status
                        except AttributeError:
                            status = retPublish.status
                        jobId = retPublish.jobId
                        dt = retPublish.status.timestamp.now()
                        startBrgTime = datetime.datetime.timestamp(dt)
                        if self.conf.debug9000:
                            logger.debug("Publication started, status: " + status)

                        if status == "PUBLISHING-INITIATED":
                            pub.status = 'IN_PROGRESS'
                            pub.start = start
                            pub.startBrgTime = startBrgTime
                            pub.hashContent = hashContent
                            pub.jobId = jobId
                        else:
                            logger.warning("Failed publication %s %s %s %s", self.url, status, jobId, self.mut.max_active)
                            self.addToReport(start, jobId, None, hashContent, status, self.url, self.mut.max_active,
                                             start_brg_time=startBrgTime, create_brg_time=None, published_brg_time=None, cif=self.cif)
                            self.mut.incLocalPublishedFail()
                        sendTime = (self.conf.getTime() - sendStartTime)
                        timeToSleep = self.conf.send_delay - sendTime
                        if timeToSleep > 0:
                            time.sleep(timeToSleep)
                    except ConnectionError as e:
                        logger.error("Sending publishDocumentRequest to {} failed - {} - {}".format(self.url, str(e), type(e)) )
                        self.addToReport(start, None, None, hashContent, 'COMMUNICATION_PROBLEM',
                                         self.url, self.mut.max_active, None, None, None, cif=self.cif)
                        self.mut.incLocalPublishedFail()
                    finally:
                        if pub.status == 'READY_TO_PUBLISH':
                            self.handleReadySlot(pub, loop_stats)
                elif pub.status == "IN_PROGRESS":
                    loop_stats.stats_checked_in_progress += 1
                    pubTime = round(self.conf.getTime() - pub.start)

                    if pubTime > self.conf.PUBLISH_TIMEOUT_S:
                        self.addToReport(
                            pub.start,
                            pub.jobId,
                            None,
                            pub.hashContent,
                            'TIMEOUT',
                            pub.publisher,
                            self.mut.max_active,
                            start_brg_time=pub.startBrgTime,
                            create_brg_time=None,
                            published_brg_time=None,
                            cif=self.cif)

                        self.mut.incLocalPublishedFail()
                        self.handleReadySlot(pub, loop_stats)
                        continue
                    start = pub.start
                    startBrgTime = pub.startBrgTime
                    jobId = pub.jobId
                    hashContent = pub.hashContent
                    try:
                        retPublishStatus = self.getEndpoint().GetPublishStatus({
                            'jobId': jobId,
                        })
                        status = retPublishStatus.status.status
                        if self.conf.debug9000:
                            logger.debug('response:' + str(retPublishStatus))
                        if status == "PUBLISHING-OK":
                            address = retPublishStatus.documentBlockchainAddress
                            createdBrgTime = Utils.getPythonTimestampFromMicrosecondsString(retPublishStatus.BLOCKCHAINpublicationDate)
                            publishedBrgTime = Utils.getPythonTimestampFromMicrosecondsString(
                                retPublishStatus.BLOCKCHAINestimMinPropagationTime)
                            self.addToReport(
                                start,
                                jobId,
                                address,
                                hashContent,
                                'FINISHED_OK',
                                self.url,
                                self.mut.max_active,
                                start_brg_time=startBrgTime,
                                create_brg_time=createdBrgTime,
                                published_brg_time=publishedBrgTime,
                                cif=self.cif)

                            self.mut.incLocalPublishedOk()
                            self.handleReadySlot(pub, loop_stats)
                            continue

                        elif status == 'PUBLISHING-INITIATED' or status == 'NOT-ADDED-TO-MAINBOX':
                            logger.debug('status:' + status)
                        else:
                            pubTime = round(self.conf.getTime() - start, self.conf.ACC)
                            logger.warning("Publication failed %s %s %s %s %s %s", self.url, jobId, pubTime, hashContent, status, self.conf.getTime())
                            self.addToReport(start, jobId, None, hashContent, status, self.url, self.mut.max_active,
                                             start_brg_time=startBrgTime, create_brg_time=None, published_brg_time=None, cif=self.cif)
                            self.mut.incLocalPublishedFail()
                            self.handleReadySlot(pub, loop_stats)
                            continue

                    except ConnectionError as e:
                        logger.error("Sending getPublishStatus to {} failed - {}".format(self.url, str(e)))
                        self.addToReport(start, jobId, None, hashContent, 'COMMUNICATION_PROBLEM', self.url, self.mut.max_active,
                                         start_brg_time=startBrgTime, create_brg_time=None, published_brg_time=None, cif=self.cif)
                        self.mut.incLocalPublishedFail()
                        self.handleReadySlot(pub, loop_stats)
                    except BaseException:
                        logger.exception("exception during getPublishStatus")
                elif pub.status == 'NOT_ACTIVE':
                    loop_stats.stats_checked_not_active += 1
                    continue
                else:
                    logger.error("Unexpected pubStatus:" + pub['status'])
        except BaseException:
            logger.exception('got unexpected exception')
            logger.error(traceback.format_exc())

    def sendPublishDocument(self, sleep_for, move_intermediate_results):
        """
        Main loop, checks all publications from slots in publicationsInProgress. Each slot handles one publication,
        after publication end new publication is started in same slot. After number of publications size of publicationsInProgress
        is adjusted based on success rate of publications.
        Loop ends when all documents are published.
        """
        logger.info("Start process for publisher {}, sleeping {}s".format(self.url, sleep_for))
        time.sleep(sleep_for)

        global global_state
        self.initSharedState()

        minimum = self.conf.min_queue_size
        maximum = self.conf.max_queue_size
        self.cif = 'no_cif'
        if self.private:
            logger.info('I will publish only private docs!')
            prv_docs_pub_mngr = PrivateDocsPublishingManager()
            publishersCif = prv_docs_pub_mngr.mapPubList(self.conf)
            if publishersCif is None:
                return False
            cycleCifList = itertools.cycle(publishersCif[self.url])
            self.cif = next(cycleCifList)
        self.mut.max_active = int((minimum + maximum) / 2)
        publicationsInProgress = []

        reserved_num = self.reserveDocumentsToPublish(self.mut.max_active)

        for _ in range(reserved_num):
            publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH'))
        self.mut.active = reserved_num

        threads_per_publisher = min(self.conf.threads_per_publisher, maximum)

        executor = concurrent.futures.ThreadPoolExecutor(threads_per_publisher)

        while self.mut.active > 0 and not global_state.exit.is_set():
            publicationsInProgress = list(filter(lambda x: x.status != 'NOT_ACTIVE', publicationsInProgress))
            cycleStart = self.conf.getTime()
            if self.mut.localPublishedOk + self.mut.localPublishedFail >= 100:
                logger.debug("Published documents:" + str(self.result.publishedOk) + "." + " Active: " + str(self.mut.active) +
                             'localPublishedFail: ' + str(self.mut.localPublishedFail) + ' localPublishedOk: ' + str(self.mut.localPublishedOk))

                old_max_active = self.mut.max_active
                self.mut.max_active = self.calculateQueue(minimum=minimum, maximum=maximum)

                self.mut.localPublishedOk = 0
                self.mut.localPublishedFail = 0
                if self.mut.max_active > old_max_active:
                    logger.info('Increasing concurrent publications from ' + str(old_max_active) + ' to ' + str(self.mut.max_active))
                    new_to_publish = self.reserveDocumentsToPublish(self.mut.max_active - old_max_active)
                    for _ in range(new_to_publish):
                        publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH'))
                        self.mut.active += 1
                elif old_max_active > self.mut.max_active:
                    logger.info('Decreasing concurrent publications from ' + str(old_max_active) + ' to ' + str(self.mut.max_active))

            loop_stats = LoopStats()
            futures = []
            for pub in publicationsInProgress:
                futures.append(executor.submit(self.processPublication, pub, loop_stats))
            concurrent.futures.wait(futures, timeout=None)

            for future in futures:
                if future.exception():
                    logger.error("Thread pool: encountered exception: %s", future.exception())
                    return False, self.result

            if self.conf.early_finish and self.mut.active < minimum:
                logger.warning("active publications number is less than min, finishing thread... {}".format(self.url))
                global_state.exit.set()
                break
            if len(self.result.publications) > 1000:
                logger.debug("%s merging results", self.url)
                move_intermediate_results(self.result)
            loopEndTime = self.conf.getTime()
            loopTime = loopEndTime - cycleStart
            timeToSleep = self.conf.SLEEP_AFTER_CHECK - loopTime
            logger.info("Url: {} loopStart: {:.3f} loopEnd: {:.3f} sleep: {:.3f}s active: {} max_active: {} loopTime: {:.3f} processed[ready:{} in_progress:{} not_active:{}] added_ready:{}".format(
                self.url, cycleStart, loopEndTime, timeToSleep, self.mut.active, self.mut.max_active, loopTime, loop_stats.stats_checked_ready, loop_stats.stats_checked_in_progress, loop_stats.stats_checked_not_active, loop_stats.added_ready))
            if self.private and loop_stats.added_ready == self.mut.active:
                self.cif = next(cycleCifList)
            if timeToSleep > 0 and self.mut.active > 0 and loop_stats.added_ready == 0:
                time.sleep(timeToSleep)
            else:
                time.sleep(0)

        move_intermediate_results(self.result)
        logger.info("%s end of thread", self.url)
        executor.shutdown()
        return True, self.result


class Utils:
    @staticmethod
    def md5(content):
        return hashlib.md5(content).hexdigest()

    @staticmethod
    def getRandomRetention():
        date = datetime.datetime.utcnow()
        rand_val = random.randint(0, 3)
        # timedelta doesn't support days, we could use dateutil.relativedelta instead
        if rand_val == 0:
            date = date + datetime.timedelta(days=20)
        elif rand_val == 1:
            date = date + datetime.timedelta(days=2*365)
        elif rand_val == 2:
            date = date + datetime.timedelta(days=12*365)
        else:
            date = date + datetime.timedelta(days=22*365)

        return int(date.timestamp()) * 10**6

    @staticmethod
    def getPythonTimestampFromMicrosecondsString(microseconds_string):
        return int(microseconds_string) / 10**6


class DocsPublishingManager:
    def __init__(self, conf):
        self.conf = conf

        self.publishers = dict()
        for ip, ports in self.conf.pubs.items():
            for port in ports:
                soap_address = 'http://' + ip + ':' + str(port)
                self.publishers[soap_address] = self.conf.documents_to_publish
        self.MAX_WORKERS = len(self.publishers)

        self.testDateStart = 0
        self.testDateEnd = 0
        self.testTime = 0
        self.timeoutTriggered = False
        self.reportName = "report_" + str(time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime()))
        self.publishedOk = 0
        self.publishedFail = 0
        self.publications = list()
        self.mean_duration = 0
        self.writtenHeader = False

    def move_intermediate_results(self, pub_state):
        with global_state.lock:
            self.writeReport(self.reportName, publications=pub_state.publications)
            pub_state.publications.clear()


    def merge_final_results(self, result):
        logger.info("finished one publisher, merging results")
        status, pub_state = result

        # multiprocess so has to be process lock
        with global_state.lock:

            if pub_state.publishedOk > 0:
                self.mean_duration = self.mean_duration + ((pub_state.publishedOk * (pub_state.mean_duration - self.mean_duration)) / (pub_state.publishedOk + self.publishedOk))

            self.publishedOk += pub_state.publishedOk
            pub_state.publishedOk = 0
            self.publishedFail += pub_state.publishedFail
            pub_state.publishedFail = 0


    def getDocumentsToPublish(self, url):
        return self.publishers[url]

    def writeReportStart(self, name):
        with open(name, 'a') as report:
            if not self.writtenHeader:
                header = "doc_hash md5 pub_task_id pub_address cif dur_time status pub_end_time threads start_brg_time create_brg_time published_brg_time dur_brg_time\n"
                report.write(header)

    def writeReportEnd(self, name, time=0):
        with open(name, 'a') as report:
            report.write("# MAX_WORKERS:" +
                         str(self.MAX_WORKERS) +
                         " max queue size::" +
                         str(self.conf.max_queue_size) +
                         " min queue size:" +
                         str(self.conf.min_queue_size) +
                         " PUBLISH_TIMEOUT_S:" +
                         str(self.conf.PUBLISH_TIMEOUT_S) +
                         " sendDelay:" +
                         str(self.conf.send_delay) +
                         " documentSize:" +
                         str(self.conf.sizeKB) +
                         "KB" +
                         " threadsPerPublisher:" +
                         str(self.conf.threads_per_publisher) +
                         "\n")
            report.write("# Successfully published documents: " + str(self.publishedOk) +
                         '/' + str(self.publishedOk + self.publishedFail) + "\n")
            report.write("# Time: " + str(time) + " seconds\n")
            report.write("# Estimated publications per 24h: " + str(int(self.publishedOk * 60 * 1440 / time)) + "\n")
            report.write("# Mean: " + str(self.mean_duration) + "\n")
            if self.publishedOk > 0:
                report.write("# Internal Score : " + str(time / self.publishedOk * self.MAX_WORKERS) + "\n")

    def writeReport(self, name, publications):
        with open(name, 'a') as report:
            for record in publications:
                rec = "{doc_hash} {md5} {pub_task_id} {pub_address} {cif} {dur_time:.3f} {status:<11} {pub_end_time} {threads} {start_brg_time:.3f} {create_brg_time:.3f} {published_brg_time:.3f} {dur_brg_time:.3f}\n".format_map(
                    record)
                report.write(rec)

    def doTimeout(self):
        logger.warning("TIMEOUT")
        global_state.exit.set()
        self.timeoutTriggered = True

    def doPreparation(self, setup_function):
        executor = concurrent.futures.ThreadPoolExecutor()
        futures = []
        for url in self.publishers.keys():
            futures.append(executor.submit(setup_function, url))
            print()  # for multiple publishers - need to do nextline
            time.sleep(1)
        concurrent.futures.wait(futures, timeout=None)
        ret = True
        for future in futures:
            if future.exception():
                logger.error("encountered exception: %s", future.exception())
                ret = False
        return ret

    def doSetup(self, url):
        pubPort = url.split(":")[-1]
        pubIp = url.split(":")[1].strip('/')
        pubEndpoint = SoapAPI.PublisherEndpoint(pubIp, pubPort)
        try:
            retSetup = pubEndpoint.Setup()
            if retSetup.status.status != 'PUBLISHING-OK':
                pubEndpoint.repeat_Setup(timeout=45 * 60)
            logger.critical('status: %s url: %s', retSetup.status.status, url)
        except ConnectionError:
            logger.exception('Unable to setup publisher: ' + str(url))
            return False
        return True

    def doCategories(self, url):
        pubPort = url.split(":")[-1]
        pubIp = url.split(":")[1].strip('/')
        pubEndpoint = SoapAPI.PublisherEndpoint(pubIp, pubPort)
        try:
            initCategoriesAnswer = pubEndpoint.wait_GetThisPublisherCategories()
        except ConnectionError:
            logger.error('Unable to setup categories on publisher: ' + str(url))
            return False
        if not SoapAPI.isAnswerOk(initCategoriesAnswer):
            logger.error('Wrong get answer for ' + str(url))
            return False
        categoriesInit = initCategoriesAnswer.categories
        if 'ROOT' in [cat.name for cat in categoriesInit]:
            return True

        categoriesInitLst = [{'name': cat.name, 'active': cat.active, 'parentPath': cat.parentPath} for cat in categoriesInit]
        categoriesToSet = [*categoriesInitLst, {'name': 'ROOT', 'active': True}]
        setAnswer = pubEndpoint.wait_SetThisPublisherCategories({'categories': categoriesToSet})
        if not SoapAPI.isAnswerOk(setAnswer):
            logger.error('Wrong set answer for ' + str(url))
            return False
        return True

    def doRun(self, private=False):
        logger.info("TestDateStart = " + str(self.testDateStart) + " no of Docs to publish #" + str(self.conf.documents_to_publish))
        self.writeReportStart(self.reportName)
        futures = []
        logger.debug("Start executor")
        with multiprocessing.Pool(self.MAX_WORKERS) as pool:
            logger.debug("Start {} processes".format(len(self.publishers)))
            for pub, sleep_for in zip(self.publishers.keys(), range(self.MAX_WORKERS, 0, -1)):
                logger.debug("Starting {}, sleep {}".format(pub, sleep_for))
                to_publish = self.getDocumentsToPublish(pub)
                single_publisher = SinglePublisherState(self.conf, pub, to_publish, private=private)
                futures.append(pool.apply_async(single_publisher.sendPublishDocument, args=(sleep_for*0.05, self.move_intermediate_results)))
            logger.debug("Started {} processes".format(len(self.publishers)))
            got_exception = False
            try:
                for future in futures:
                    self.merge_final_results(future.get())
            except Exception as ex:
                got_exception = True
                logging.exception("exception from publishing process")

            pool.close()
            pool.join()

        self.testDateEnd = self.conf.getTime()
        self.testTime = round(self.testDateEnd - self.testDateStart, self.conf.ACC)
        logger.critical('Test time:' + str(self.testTime))
        logger.critical('Opublikowano ' + str(self.publishedOk) + '/' + str(self.publishedOk + self.publishedFail) + ' dokumentów.')
        self.writeReport(self.reportName, publications=self.publications)
        self.writeReportEnd(self.reportName, self.testTime)
        if self.timeoutTriggered or self.publishedFail > 0 or got_exception:
            return False
        return True


def setupLogger(log_level, verbose):
    logger.setLevel(log_level)
    # basicConfig would get logs from zeep

    # file logging
    time_now_str = datetime.datetime.now().strftime('%Y_%m_%d_%H_%M_%S')
    file_handler = logging.FileHandler(filename='DurableMediaTestLog_' + time_now_str)
    formatter = logging.Formatter('[%(asctime)s][%(name)18s][%(thread)d][%(levelname)8s] %(message)s [%(filename)s:%(lineno)d]')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    # console logging
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING if not verbose else logging.INFO)
    formatter = logging.Formatter('[%(levelname)-4s] %(message)s')
    console.setFormatter(formatter)
    logger.addHandler(console)


def main(params):

    conf = Config()
    conf.readConfFromArgparse(params)

    global global_state
    global_state = GlobalState()

    setupLogger(conf.loglevel, conf.verbose)

    docs_pub_mngr = DocsPublishingManager(conf)
    docs_pub_mngr.testDateStart = docs_pub_mngr.conf.getTime()
    timeoutTimer = None
    try:
        logger.debug("action: %s", conf.action)
        logger.debug("publishers: %s", docs_pub_mngr.conf.pubs)
        if conf.timeout:
            timeoutTimer = threading.Timer(conf.timeout, docs_pub_mngr.doTimeout)
            timeoutTimer.start()
        if conf.action == 'setup':
            return docs_pub_mngr.doPreparation(docs_pub_mngr.doSetup)
        elif conf.action == 'categories':
            return docs_pub_mngr.doPreparation(docs_pub_mngr.doCategories)
        elif conf.action == 'run':
            signal.signal(signal.SIGINT, signal_handler)
            return docs_pub_mngr.doRun(conf.private)
        elif conf.action == 'noop':
            return True
        return False

    except KeyboardInterrupt:
        docs_pub_mngr.testDateEnd = docs_pub_mngr.conf.getTime()
        docs_pub_mngr.testTime = round(docs_pub_mngr.testDateEnd - docs_pub_mngr.testDateStart, docs_pub_mngr.conf.ACC)
        global_state.exit.set()
        docs_pub_mngr.writeReport(docs_pub_mngr.reportName, docs_pub_mngr.publications)
        sys.exit(3)
    except Exception:
        logger.exception("outer scope excepion")
        sys.exit(2)
    finally:
        if timeoutTimer:
            timeoutTimer.cancel()


if __name__ == "__main__":
    ok = main(sys.argv[1:])
    sys.exit(0 if ok else 1)
