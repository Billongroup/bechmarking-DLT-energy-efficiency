#!/usr/bin/env python3
# coding=utf-8
# Billon 2023

# QUICK README
"""
Script for publishing public and private documents with random content.
Main parameters (see Config):
    documentsToPublish - number of documents to publish per publisher (cannot cannot exceed the number of predefined pdf)
    use_predefined_pdfs - whether to use predefined pdf files
    pdf_dir - path to directory with pdfs to publish (not necessary for random documents test)
    sizeKB - size of document (only for documents test)
    max_queue_size - max number of publications in progress
    min_queue_size - min number of publications in progress
    identitiesFilename - file with identities to be used for private documents

Script generates raport and writes it to the file

Usage examples: (Step 1 and 2 are obligatory, without setup publication is disabled)
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
    pdfs folder with pdf files to publish (not necessary for random documents test)
"""
from requests import RequestException
import base64

try:
    from DurableMediaTestConfig import Config
    from soap import SoapAPI
except ImportError:
    from colony_scripts.colony.tests.DurableMediaTestConfig import Config
    from colony_scripts.colony.tests.soap import SoapAPI

import argparse
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
import base58
import glob
from collections import defaultdict
from pathlib import Path
# from pdfrw import PdfReader, PdfWriter
from io import BytesIO
from threading import Lock

logger = logging.getLogger("DurableMediaTest")

global_state = None

mutex = Lock()

def signal_handler(sig, frame):
    logger.warning('Exiting, signal {} called'.format(sig))
    global_state.exit.set()



class ExtendedDocsPublishingManager:

    def getEndpoint(self, ip, port):
        endpoint = SoapAPI.PublisherEndpoint(ip, port)
        return endpoint

    def getPublisherId(self, endpoint):
        ans = endpoint.Hello()
        return ans.publisherId

    def cyclePubHashForMe(self, conf, myURL):
        listOfOtherPubs = list()
        for ip, ports in conf.rcv_publishers.items():
            for port in ports:
                soap_address = 'http://' + ip + ':' + str(port)
                if soap_address != myURL:
                    endpoint = self.getEndpoint(ip, port)
                    try:
                        pubId = self.getPublisherId(endpoint)
                        listOfOtherPubs.append(pubId)
                    except Exception as e:
                        logger.error('Unable to get publisherId for endpoint! ' + str(ip) + ' ' + str(port))

        return itertools.cycle(listOfOtherPubs)

    def cyclePubUrlForMe(self, conf, myURL):
        listOfOtherPubs = list()
        for ip, ports in conf.rcv_publishers.items():
            for port in ports:
                soap_address = 'http://' + ip + ':' + str(port)
                if soap_address != myURL:
                    listOfOtherPubs.append(soap_address)
        return itertools.cycle(listOfOtherPubs)

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
    def __init__(self, publisher, status, blockchainAddress = None, updateCount = 0, readerEndpoint = None, readerUrl = None):
        self.publisher = publisher
        self.status = status
        self.start = None
        self.jobId = None
        self.startBrgTime = 0
        self.hashContent = None
        self.blockchainAddress = blockchainAddress
        self.taskId = None
        self.createdBrgTime = 0
        self.publishedBrgTime = 0
        self.readStart = None
        self.timeToInit = 0
        self.updateCount = updateCount
        self.updateConst = updateCount
        self.cif = 'no_cif'
        self.readerEndpoint = readerEndpoint
        self.readerUrl = readerUrl
        self.content = []
        self.additional_details = ''
        self.documentMainCategory = 'ROOT'
        self.title = 'Random title'

    def resetToReadyToPublish(self):
        self.updateCount -= 1
        if self.updateCount < 0 or self.blockchainAddress == None:
            self.blockchainAddress = None
            self.updateCount = self.updateConst
        self.status = 'READY_TO_PUBLISH'
        self.start = None
        self.jobId = None
        self.startBrgTime = None
        self.hashContent = None
        #self.blockchainAddress = None
        self.taskId = None
        self.createdBrgTime = None
        self.publishedBrgTime = None
        self.readStart = None
        self.timeToInit = 0
        self.cif = 'no_cif'
        #self.readerEndpoint = None

    def resetToReadyToRead(self):
        self.resetToReadyToPublish()
        #self.blockchainAddress = blockchainAddress
        self.blockchainAddress = None
        self.status = 'READY_TO_READ'
        self.startBrgTime = 0
        self.createdBrgTime = 0
        self.publishedBrgTime = 0
        self.timeToInit = 0
        self.readerEndpoint = None

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
    def __init__(self, csv_reader = None):
        self.publisherLock = threading.Lock()
        self.fileLock = threading.Lock()
        self.docHashLock = threading.Lock()

        self.index = 0
        self.localPublishedFail = 0
        self.localPublishedOk = 0
        self.active = 0
        self.max_active = 0
        self.csv_reader = csv_reader
        self.doc_hashes = []

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

    def getNextAddr(self):
        with self.docHashLock:
            return self.doc_hashes.pop()

    def readAddrFromFile(self):
        with self.fileLock:
            with self.docHashLock:
                self.doc_hashes.append(self.csv_reader.__next__()[0])


class SinglePublisherState:
    def __init__(self, conf, url, to_publish, private=False, reportCatalog = None, readUrl = None):
        self.conf = conf
        self.result = SinglePublisherResult()
        self.to_publish = to_publish
        self.binaries = {}
        self.trailer = None
        self.url = url
        self.readerUrl = readUrl
        self.private = private
        self.mut: MutatingPublisherState = None
        self.cif = None
        self.endpoint = None
        self.readerEndpoint = None
        self.read_after = self.conf.read_after
        self.reportCatalog = reportCatalog
        if reportCatalog:
            self.reportCatalog += '/'
        self.gen = None
        # TODO stworzenie generatora dokumentow do publikacji

    def initSharedState(self):
        if self.conf.csv_file is not None:
            file = open(Utils.md5(str.encode(self.url)) + '.csv', 'r')
            self.mut = MutatingPublisherState(csv_reader=csv.reader(file))
        else:
            self.mut = MutatingPublisherState()

    def getEndpoint(self):
        if self.endpoint is None:
            pubPort = self.url.split(":")[-1]
            pubIp = self.url.split(":")[1].strip('/')
            self.endpoint = SoapAPI.PublisherEndpoint(pubIp, pubPort)
        return self.endpoint

    def getReaderEndpoint(self):
        url = next(self.readerUrl)
        pubPort = url.split(":")[-1]
        pubIp = url.split(":")[1].strip('/')
        self.readerEndpoint = SoapAPI.PublisherEndpoint(pubIp, pubPort)
        return self.readerEndpoint, url

    def printProgress(self, width = 50, percents = None, concurrent=None):
        finished = self.result.publishedOk + self.result.publishedFail
        if percents is None:
            percents = finished / (self.conf.documents_to_publish * (1 + self.conf.update_immediate)) * 100
        filled = int(percents / 100 * width) * '#'
        empty = int((width - len(filled))) * '-'
        progress_bar = filled + empty
        sys.stdout.write('\r%s |%s| %.2f%s %s' % ('[', progress_bar, percents, '%', ']'))
        if(concurrent):
            sys.stdout.write(' concurrent: %s' % (concurrent))
        sys.stdout.flush()

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
            time_to_init,
            cif='no_cif',
            read_time = None,
            prev_doc = None,
            published_by = None,
            read_by=None):
        pubTime = round(self.conf.getTime() - start, self.conf.ACC)
        pubEndTime = self.conf.getTime()

        if status == 'PUBLISHING-SUBSYSTEM-LOW-DISK-SPACE':
            logger.warning("LOW DISK SPACE")
            global_state.exit.set()

        if blockchainAddress is None:
            blockchainAddress = '------------------------------------------------'
        if jobId is None:
            jobId = '------------------------'
        if read_time is None:
            read_time = 'NA'
        if prev_doc is None:
            prev_doc = 'NA'
        if published_by is None:
            published_by = 'NA'
        if read_by is None:
            read_by = 'NA'
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
            'time_to_init': time_to_init,
            'dur_brg_time': duration_brg_time,
            'dur_read_time': read_time,
            'prev_doc': prev_doc,
            'published_by': published_by,
            'read_by': read_by
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

    def reserveDocumentsToRead(self, to_reserve):
        successfullyReserved = 0
        for i in range(to_reserve):
            try:
                self.mut.readAddrFromFile()
                successfullyReserved += 1
            except StopIteration as e:
                self.to_publish = 0
                break
        return successfullyReserved

    def reserveDocumentsToPublish(self, to_reserve):
        if self.conf.test_duration > 0:
            return to_reserve
        if self.conf.csv_file:
            return self.reserveDocumentsToRead(to_reserve)
        to_publish_before = self.to_publish
        max_to_publish = min(self.to_publish, to_reserve)
        self.to_publish -= max_to_publish

        logger.debug('Want to reserve: {} from: {}, reserved: {}'.format(to_reserve, to_publish_before, max_to_publish))

        return max_to_publish

    def getRandomContent(self, sizeKB, suffix):
        # lock?
        if sizeKB not in self.binaries:
            self.binaries[sizeKB] = os.urandom(1024 * sizeKB)
        if self.conf.pdf_file is not None:
            from pdfrw import PdfReader, PdfWriter
            if self.trailer is None:
                trailer = PdfReader(self.conf.pdf_file)
            trailer.Info.WhoAmI = str.encode(suffix) + self.binaries[sizeKB]

            myio = BytesIO()
            PdfWriter(trailer=trailer).write(myio)
            myio.seek(0)
            result = myio.getvalue()
        else:
            result = str.encode("%PDF-1.1") + str.encode(suffix) + self.binaries[sizeKB]
        return [result]

    def getNextPdf(self, sufix=""):
        with mutex:
            if self.gen is None:
                self.gen = self.conf.pdf_getter(self.url)
            additional_details = 'Here be PUBLIC additional details'
            documentMainCategory = 'ROOT'
            title = None
            if not self.conf.use_predefined_pdfs:
                content = self.getRandomContent(int(self.conf.sizeKB), sufix)
                return content, additional_details, documentMainCategory, title
            try:
                from pdfrw import PdfReader, PdfWriter
                pdf_path, additional_details, documentMainCategory, title = next(self.gen)
            except Exception as err:
                print(err)
                raise err
                return None, additional_details,  documentMainCategory, title
            result_pdfs = []
            for pdf in pdf_path:
                trailer = PdfReader(pdf)
                myio = BytesIO()
                PdfWriter(trailer=trailer).write(myio)
                myio.seek(0)
                result_pdfs.append(myio.getvalue())
        return result_pdfs, additional_details, documentMainCategory, title

    def calculateQueue(self, minimum, maximum):
        max_active_now = self.mut.max_active
        if maximum < 0:
            if self.mut.localPublishedOk / (self.mut.localPublishedOk + self.mut.localPublishedFail) < 0.95:
                maximum = max_active_now
            else:
                step = maximum *-1
                max_active_now += step
        else:
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
        return max_active_now, maximum

    def handleReadySlot(self, pub: PublicationSlot, loop_stats: LoopStats):
        with self.mut.publisherLock:
            if self.mut.active <= self.mut.max_active and (pub.updateCount > 0 or self.reserveDocumentsToPublish(1) == 1):
                loop_stats.added_ready += 1
                if self.conf.read_only:
                    pub.resetToReadyToRead()
                else:
                    pub.resetToReadyToPublish()
                return True
            pub.setNotActive()
            self.mut.active -= 1
            return False

    def processPublication(self, pub: PublicationSlot, loop_stats, threadNo):
        global global_state
        try:
            repeatPub = True
            while repeatPub and not global_state.exit.is_set():
                repeatPub = False
                logger.debug("pubStatus: " + pub.status)
                # Did not start publishing yet
                if pub.status == 'READY_TO_PUBLISH':
                    loop_stats.stats_checked_ready += 1
                    start = self.conf.getTime()
                    index = self.mut.incGetIndex()
                    if not pub.content:
                        pub.content, pub.additional_details, pub.documentMainCategory, pub.title = self.getNextPdf(str(index) + 'A' + str(threadNo) + 'time' + str(start) + '@' + self.url)
                        if len(pub.content) > 1:
                            pub.updateCount = len(pub.content) - 1
                    if pub.content is None:
                        raise BaseException('Problem with pdf getter')
                    pub.content.reverse()
                    creationDate = str(int(start) * 10**6)
                    hashContent = Utils.md5(pub.content[-1])
                    randomText = 'RandomText_i' + str(index) + 't' + str(threadNo) + '@' + self.url
                    title = pub.title or randomText
                    try:
                        sendStartTime = self.conf.getTime()
                        retentionDate = Utils.getRandomRetention()
                        if self.conf.private_for_publisher:
                            pub.cif = next(self.cif)
                            retPublish = self.getEndpoint().PublishPrivateDocument({
                                'publisherCif': 'none',
                                'publicationMode': 'NEW',
                                'documentData': {
                                    'title': title,
                                    'sourceDocument': pub.content.pop(),
                                    'documentMainCategory': pub.documentMainCategory,
                                    'documentSystemCategory': 'ROOT',
                                    'BLOCKCHAINlegalValidityStartDate': creationDate,
                                    'BLOCKCHAINexpirationDate': retentionDate,
                                    'BLOCKCHAINretentionDate': retentionDate,
                                    'extension': 'PDF',
                                    'additionalDetails': pub.additional_details,
                                    'privateAdditionalDetails': 'Here be PRIVATE additional details',
                                },
                                'authorizedUsersList' : pub.cif,
                                'sendAuthorizationCodes': 'true',
                            })
                        elif self.private:

                            retPublish = self.getEndpoint().PublishPrivateDocument({
                                'publisherCif': self.cif,
                                'publicationMode': 'NEW',
                                'documentData': {
                                    'title': title,
                                    'sourceDocument': pub.content.pop(),
                                    'documentMainCategory': pub.documentMainCategory,
                                    'documentSystemCategory': 'ROOT',
                                    'BLOCKCHAINlegalValidityStartDate': creationDate,
                                    'BLOCKCHAINexpirationDate': retentionDate,
                                    'BLOCKCHAINretentionDate': retentionDate,
                                    'extension': 'PDF',
                                    'additionalDetails': pub.additional_details,
                                    'privateAdditionalDetails': 'Here be PRIVATE additional details',
                                },
                                'sendAuthorizationCodes': 'true',
                            })
                        else:
                            if pub.blockchainAddress is None and self.conf.update:
                                pub.blockchainAddress = self.mut.getNextAddr()
                            if pub.blockchainAddress is not None:
                                retPublish = self.getEndpoint().PublishPublicDocument({
                                    'publicationMode': 'UPDATED',
                                    'documentData': {
                                        'title': title,
                                        'previousDocumentBlockchainAddress': pub.blockchainAddress,
                                        'sourceDocument': pub.content.pop(),
                                        'documentMainCategory': pub.documentMainCategory,
                                        'documentSystemCategory': 'ROOT',
                                        'BLOCKCHAINlegalValidityStartDate': creationDate,
                                        'BLOCKCHAINexpirationDate': retentionDate,
                                        'BLOCKCHAINretentionDate': retentionDate,
                                        'extension': 'PDF',
                                    }
                                })
                            else:
                                retPublish = self.getEndpoint().PublishPublicDocument({
                                    'publicationMode': 'NEW',
                                    'documentData': {
                                        'title': title,
                                        'sourceDocument': pub.content.pop(),
                                        'documentMainCategory': pub.documentMainCategory,
                                        'documentSystemCategory': 'ROOT',
                                        'BLOCKCHAINlegalValidityStartDate': creationDate,
                                        'BLOCKCHAINexpirationDate': retentionDate,
                                        'BLOCKCHAINretentionDate': retentionDate,
                                        'extension': 'PDF',
                                        'additionalDetails': pub.additional_details,
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
                            pub.timeToInit = self.conf.getTime() - sendStartTime
                            # self.addToReport(start, jobId, None, hashContent, status, self.url, self.mut.max_active,
                            #                  start_brg_time=startBrgTime, create_brg_time=None, published_brg_time=None, cif=self.cif)
                        else:
                            logger.warning("Failed publication %s %s %s %s %s %s", self.url, status, jobId, self.mut.max_active, threadNo, index)
                            self.addToReport(start, jobId, None, hashContent, status, self.url, self.mut.max_active,
                                             start_brg_time=startBrgTime, create_brg_time=None, published_brg_time=None, time_to_init=0, cif=pub.cif)
                            self.mut.incLocalPublishedFail()
                        sendTime = (self.conf.getTime() - sendStartTime)
                        timeToSleep = self.conf.send_delay - sendTime
                        if timeToSleep > 0:
                            time.sleep(timeToSleep)
                    except RequestException as e:
                        logger.error("Sending publishDocumentRequest to {} failed - {} - {}".format(self.url, str(e), type(e)) )
                        self.addToReport(start, None, None, hashContent, 'COMMUNICATION_PROBLEM',
                                         self.url, self.mut.max_active, 0, 0, 0, 0, cif=pub.cif)
                        self.mut.incLocalPublishedFail()
                        return False
                    except ConnectionError as e:
                        logger.error("Sending publishDocumentRequest to {} failed - {} - {}".format(self.url, str(e), type(e)) )
                        self.addToReport(start, None, None, hashContent, 'COMMUNICATION_PROBLEM',
                                         self.url, self.mut.max_active, 0, 0, 0, 0, cif=pub.cif)
                        self.mut.incLocalPublishedFail()
                        return False
                    finally:
                        if pub.status == 'READY_TO_PUBLISH':
                            self.handleReadySlot(pub, loop_stats)
                elif pub.status == 'READY_TO_READ':
                    loop_stats.stats_checked_in_progress += 1
                    if pub.start is None:
                        pub.start = self.conf.getTime()
                    pubTime = round(self.conf.getTime() - pub.start)
                    if pubTime > self.conf.PUBLISH_TIMEOUT_S:
                        self.addToReport(
                            pub.start,
                            pub.jobId,
                            pub.blockchainAddress,
                            pub.hashContent,
                            'READ_TIMEOUT',
                            pub.publisher,
                            self.mut.max_active,
                            start_brg_time=pub.startBrgTime,
                            create_brg_time=pub.createdBrgTime,
                            published_brg_time=pub.publishedBrgTime,
                            time_to_init=pub.timeToInit,
                            cif=pub.cif)

                        self.mut.incLocalPublishedFail()
                        self.handleReadySlot(pub, loop_stats)
                        continue

                    if pub.readStart is None:
                        pub.readStart = self.conf.getTime()

                    try:
                        if pub.blockchainAddress is None:
                            pub.blockchainAddress = self.mut.getNextAddr()
                        if pub.readerEndpoint is None:
                            pub.readerEndpoint, pub.readerUrl = self.getReaderEndpoint()
                        retRead = pub.readerEndpoint.GetDocument({
                            'documentType': 'PUBLIC',
                            'documentBlockchainAddress': pub.blockchainAddress
                        })
                        readTime = self.conf.getTime()
                        if retRead.status.status == 'PUBLISHING-OK':
                            if "previousDocumentBlockchainAddress" in retRead.documentInfo.documentData.__values__.keys():
                                prev_doc = retRead.documentInfo.documentData.previousDocumentBlockchainAddress
                            else:
                                prev_doc = 'NA'
                            publishedBy = retRead.documentInfo.documentBlockchainData.publisherId
                            self.mut.incLocalPublishedOk()
                            if self.conf.write_on_disk:
                                with open(self.reportCatalog + str(pub.blockchainAddress), 'wb') as file:
                                    file.write(retRead.documentInfo.documentData.sourceDocument)
                            self.addToReport(
                                pub.start,
                                pub.jobId,
                                pub.blockchainAddress,
                                pub.hashContent,
                                'FINISHED_OK',
                                self.url,
                                self.mut.max_active,
                                start_brg_time=pub.startBrgTime,
                                create_brg_time=pub.createdBrgTime,
                                published_brg_time=pub.publishedBrgTime,
                                time_to_init=pub.timeToInit,
                                cif=pub.cif,
                                read_time = readTime - pub.readStart,
                                prev_doc = prev_doc,
                                published_by = publishedBy,
                                read_by = pub.readerUrl)
                            self.handleReadySlot(pub, loop_stats)
                            repeatPub = True
                            continue
                        elif retRead.status.status == 'SEARCH-INITIATED' or 'SEARCH-IN-PROGRESS' == retRead.status.status:
                            continue
                        else:
                            logger.warning("couldnt read %s", retRead.status.status)
                            self.mut.incLocalPublishedFail()
                            self.addToReport(
                                pub.start,
                                pub.jobId,
                                pub.blockchainAddress,
                                pub.hashContent,
                                'COULDNT_READ',
                                self.url,
                                self.mut.max_active,
                                start_brg_time=pub.startBrgTime,
                                create_brg_time=pub.createdBrgTime,
                                published_brg_time=pub.publishedBrgTime,
                                time_to_init=pub.timeToInit,
                                cif=pub.cif)

                        self.handleReadySlot(pub, loop_stats)
                        continue

                    except RequestException as e:
                        logger.error("Sending getDocument to {} failed - {} - {}".format(self.url, str(e), type(e)) )
                        self.addToReport(start, None, None, hashContent, 'COMMUNICATION_READ_PROBLEM',
                                         self.url, self.mut.max_active, 0, 0, 0, 0, cif=pub.cif)
                        self.mut.incLocalPublishedFail()

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
                            time_to_init=pub.timeToInit,
                            cif=pub.cif)

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
                            prev_addr = pub.blockchainAddress
                            pub.blockchainAddress = retPublishStatus.documentBlockchainAddress
                            pub.createdBrgTime = Utils.getPythonTimestampFromMicrosecondsString(retPublishStatus.BLOCKCHAINpublicationDate)
                            pub.publishedBrgTime = Utils.getPythonTimestampFromMicrosecondsString(
                                retPublishStatus.BLOCKCHAINestimMinPropagationTime)
                            if self.read_after:
                                pub.status = 'READY_TO_READ'
                                repeatPub = True
                                continue
                            self.addToReport(
                                start,
                                jobId,
                                pub.blockchainAddress,
                                hashContent,
                                'FINISHED_OK',
                                self.url,
                                self.mut.max_active,
                                start_brg_time=startBrgTime,
                                create_brg_time=pub.createdBrgTime,
                                published_brg_time=pub.publishedBrgTime,
                                time_to_init=pub.timeToInit,
                                cif=pub.cif,
                                prev_doc= prev_addr)

                            self.mut.incLocalPublishedOk()
                            self.handleReadySlot(pub, loop_stats)
                            repeatPub = True
                            continue

                        elif status == 'PUBLISHING-INITIATED' or status == 'NOT-ADDED-TO-MAINBOX':
                            logger.debug('status:' + status)
                        else:
                            pubTime = round(self.conf.getTime() - start, self.conf.ACC)
                            logger.warning("Publication failed %s %s %s %s %s %s", self.url, jobId, pubTime, hashContent, status, self.conf.getTime())
                            self.addToReport(start, jobId, None, hashContent, status, self.url, self.mut.max_active,
                                             start_brg_time=startBrgTime, create_brg_time=None, published_brg_time=None,time_to_init=0, cif=pub.cif)
                            self.mut.incLocalPublishedFail()
                            self.handleReadySlot(pub, loop_stats)
                            continue

                    except RequestException as e:
                        logger.error("Sending getPublishStatus to {} failed - {}".format(self.url, str(e)))
                        self.addToReport(start, jobId, None, hashContent, 'COMMUNICATION_PROBLEM', self.url, self.mut.max_active,
                                         start_brg_time=startBrgTime, create_brg_time=0, published_brg_time=0, time_to_init=0, cif=pub.cif)
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
        return True

    def sendPublishDocument(self, sleep_for, move_intermediate_results, printProgress = False):
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
        startTime = self.conf.getTime()
        minimum = self.conf.min_queue_size
        maximum = self.conf.max_queue_size
        self.cif = 'no_cif'
        if self.private:
            logger.info('I will publish only private docs!')
            ext_docs_pub_mngr = ExtendedDocsPublishingManager()
            publishersCif = ext_docs_pub_mngr.mapPubList(self.conf)
            if publishersCif is None:
                return False
            cycleCifList = itertools.cycle(publishersCif[self.url])
            self.cif = next(cycleCifList)
        if self.conf.private_for_publisher:
            ext_docs_pub_mngr = ExtendedDocsPublishingManager()
            self.cif = ext_docs_pub_mngr.cyclePubHashForMe(self.conf, self.url)
        if self.conf.read_after:
            ext_docs_pub_mngr = ExtendedDocsPublishingManager()
            self.readerUrl = ext_docs_pub_mngr.cyclePubUrlForMe(self.conf, self.url)
        self.mut.max_active = int((minimum + maximum) / 2)
        if(maximum < 0):
            self.mut.max_active = minimum
        publicationsInProgress = []

        reserved_num = self.reserveDocumentsToPublish(self.mut.max_active)

        for _ in range(reserved_num):
            if self.conf.read_only:
                publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_READ'))
            elif self.conf.update:
                publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH', blockchainAddress=self.mut.getNextAddr()))
            elif self.conf.update_immediate > 0:
                publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH', updateCount=self.conf.update_immediate))
            else:
                if self.conf.read_after:
                    readerEndpoint, readerUrl = self.getReaderEndpoint()
                    publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH', readerUrl=readerUrl, readerEndpoint = readerEndpoint))
                else:
                    publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH'))
        self.mut.active = reserved_num

        threads_per_publisher = min(self.conf.threads_per_publisher, maximum)
        if threads_per_publisher < 0:
            threads_per_publisher = self.conf.threads_per_publisher
        executor = concurrent.futures.ThreadPoolExecutor(threads_per_publisher)
        threadNo = 0

        while self.mut.active > 0 and not global_state.exit.is_set():
            publicationsInProgress = list(filter(lambda x: x.status != 'NOT_ACTIVE', publicationsInProgress))
            cycleStart = self.conf.getTime()
            if self.mut.localPublishedOk + self.mut.localPublishedFail >= 100:
                logger.debug("Published documents:" + str(self.result.publishedOk) + "." + " Active: " + str(self.mut.active) +
                             'localPublishedFail: ' + str(self.mut.localPublishedFail) + ' localPublishedOk: ' + str(self.mut.localPublishedOk))

                old_max_active = self.mut.max_active
                self.mut.max_active, maximum = self.calculateQueue(minimum=minimum, maximum=maximum)

                self.mut.localPublishedOk = 0
                self.mut.localPublishedFail = 0
                if self.mut.max_active > old_max_active:
                    logger.info('Increasing concurrent publications from ' + str(old_max_active) + ' to ' + str(self.mut.max_active))
                    new_to_publish = self.reserveDocumentsToPublish(self.mut.max_active - old_max_active)
                    for _ in range(new_to_publish):
                        if self.conf.read_only:
                            publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_READ'))
                        elif self.conf.update:
                            publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH', blockchainAddress=self.mut.getNextAddr()))
                        elif self.conf.update_immediate > 0:
                            publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH', updateCount=self.conf.update_immediate))
                        else:
                            publicationsInProgress.append(PublicationSlot(publisher=self.url, status='READY_TO_PUBLISH'))
                        self.mut.active += 1
                elif old_max_active > self.mut.max_active:
                    logger.info('Decreasing concurrent publications from ' + str(old_max_active) + ' to ' + str(self.mut.max_active))

            loop_stats = LoopStats()
            futures = []
            for pub in publicationsInProgress:
                # content, additional_details, documentMainCategory, title = self.getNextPdf()
                futures.append(executor.submit(self.processPublication, pub, loop_stats, threadNo))
                threadNo += 1
            concurrent.futures.wait(futures, timeout=None)

            for future in futures:
                if future.exception():
                    logger.error("Thread pool: encountered exception: %s", future.exception())
                    return False, self.result
                if not future.result():
                    logger.error("Thread pool: encountered exception: %s", future.exception())
                    return False, self.result

            if self.conf.test_duration > 0 and self.conf.getTime() - startTime > self.conf.test_duration:
                logger.warning("time passed, finishing thread... {}".format(self.url))
                global_state.exit.set()
                break
            if self.conf.early_finish and self.mut.active < minimum:
                logger.warning("active publications number is less than min, finishing thread... {}".format(self.url))
                global_state.exit.set()
                break
            if len(self.result.publications) > 1000:
                logger.debug("%s merging results", self.url)
                move_intermediate_results(self.result)
            if printProgress:
                if self.conf.test_duration > 0:
                    self.printProgress(percents=(self.conf.getTime() - startTime) / self.conf.test_duration *100, concurrent=self.mut.max_active)
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

    # @staticmethod
    # def getNextPdf():
    #     pdfs = glob.glob('*pdf')
    #     for pdf in pdfs:
    #         with open(pdf, "rb") as pdf_file:
    #             pdf_data = pdf_file.read()
    #         yield base64.b64encode(pdf_data).decode("utf-8")

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
        self.reportName = "report_" + str(time.strftime("%Y_%m_%d_%H_%M_%S", time.localtime())) + '.csv'
        self.publishedOk = 0
        self.publishedFail = 0
        self.publications = list()
        self.mean_duration = 0
        self.writtenHeader = False

    def prepare_reading_csv(self):
        writerForublishers = {}
        for addr, value in self.publishers.items():
            print("addr")
            print(addr)
            file = open(Utils.md5(str.encode(addr)) + '.csv', 'w')
            writerForublishers[addr] = csv.writer(file, delimiter = '\t')

        linenum = 0
        with open(self.conf.csv_file, 'r') as file:
            csv_file = csv.DictReader(file)
            for row in csv_file:
                try:
                    base58.b58decode(row["doc_hash"])
                    list(writerForublishers.values())[linenum % len(self.publishers.items())].writerow([row["doc_hash"]])
                    linenum += 1
                except:
                    pass
        # with open(self.conf.csv_file, 'r') as file:
        #     csv_file = csv.DictReader(file)
        #     for row in csv_file:
        #         try:
        #             base58.b58decode(row["doc_hash"])
        #             # print("pub_address")
        #             # print(row)
        #             print(row["pub_address"])
        #             writerForublishers[row["pub_address"]].writerow([row["doc_hash"]])
        #         except:
        #             pass

    def delete_reading_csv(self):
        pass

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
                header = "doc_hash,md5,pub_task_id,pub_address,cif,dur_time,status,pub_end_time,threads,start_brg_time,create_brg_time,published_brg_time,dur_brg_time,time_to_init,read_time,prev_doc_hash,published_by,read_by\n"
                report.write(header)
            if self.conf.write_on_disk:
                os.makedirs(Path(name).stem, exist_ok=True)

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
                rec = "{doc_hash},{md5},{pub_task_id},{pub_address},{cif},{dur_time:.3f},{status:<11},{pub_end_time},{threads},{start_brg_time:.3f},{create_brg_time:.3f},{published_brg_time:.3f},{dur_brg_time:.3f},{time_to_init:.3f},{dur_read_time},{prev_doc},{published_by},{read_by}\n".format_map(
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
        categoriesToSet = [*categoriesInitLst, {'name': 'ROOT', 'active': True}, *[{'name': cat, 'active': True} for cat in self.conf.pubs_categories[url]]]
        setAnswer = pubEndpoint.wait_SetThisPublisherCategories({'categories': categoriesToSet})
        if not SoapAPI.isAnswerOk(setAnswer):
            logger.error('Wrong set answer for ' + str(url))
            return False
        return True

    def doRun(self, private=False):
        if self.conf.csv_file:
            self.prepare_reading_csv()
        logger.info("TestDateStart = " + str(self.testDateStart) + " no of Docs to publish #" + str(self.conf.documents_to_publish))
        self.writeReportStart(self.reportName)
        futures = []
        logger.debug("Start executor")
        with multiprocessing.Pool(self.MAX_WORKERS) as pool:
            logger.debug("Start {} processes".format(len(self.publishers)))
            printProgress = True
            pubReader = {}
            pubs = list(self.publishers.keys())
            pubs += [pubs.pop(0)]
            i = 0
            if self.conf.rcv_publishers is not None:
                readerList = list()
                for ip, ports in self.conf.rcv_publishers.items():
                    for port in ports:
                        soap_address = 'http://' + ip + ':' + str(port)
                        readerList.append(soap_address)

            for pub in self.publishers.keys():
                if self.conf.rcv_publishers is not None:
                    pubReader[pub] = readerList[i]
                    print(readerList[i])
                else:
                    pubReader[pub] = pubs[i]
                i += 0
            for pub, sleep_for in zip(self.publishers.keys(), range(self.MAX_WORKERS, 0, -1)):
                logger.debug("Starting {}, sleep {}".format(pub, sleep_for))
                to_publish = self.getDocumentsToPublish(pub)
                single_publisher = SinglePublisherState(self.conf, pub, to_publish, private=private, reportCatalog=Path(self.reportName).stem, readUrl=pubReader[pub])
                futures.append(pool.apply_async(single_publisher.sendPublishDocument, args=(sleep_for*0.05, self.move_intermediate_results, printProgress)))
                printProgress = False
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
        if self.conf.read_only:
            logger.critical('Read ' + str(self.publishedOk) + '/' + str(self.publishedOk + self.publishedFail) + ' documents.')
        else:
            logger.critical('Published ' + str(self.publishedOk) + '/' + str(self.publishedOk + self.publishedFail) + ' documents.')
        logger.critical('Results written to file: ' + self.reportName)
        self.writeReport(self.reportName, publications=self.publications)
        self.writeReportEnd(self.reportName, self.testTime)
        if self.conf.csv_file:
            self.delete_reading_csv()
        if self.timeoutTriggered or self.publishedFail > 0 or got_exception:
            return False
        return True


class DurableMediaTester:
    def __init__(self, params):
        parser = argparse.ArgumentParser()
        parser.add_argument('action', help='action to execute', choices=['setup', 'categories', 'run', 'noop'], nargs='?', default='run')
        parser.add_argument('-c', '--configFile', help='path to config.py of colony')
        parser.add_argument('-n', '--num_publications', help='how many documents will be published', type=int)
        parser.add_argument('-s', '--size', help='size of publications [kB]', type=int)
        parser.add_argument('-p', '--packet_size', help='max number of concurrent publications', type=int)
        parser.add_argument('-m', '--min_packet_size', help='min number of concurrent publications', type=int)
        parser.add_argument('-t', '--timeout', help='number of seconds before finishing with failure', type=int)
        parser.add_argument('--publishers', help='override publishers config')
        parser.add_argument('--publishers_limit', help='only select a few first publishers from config', type=int)
        parser.add_argument('--private', help='execute private docs publishing', action='store_true', default=False)
        parser.add_argument('--identities', action='store', type=str, help='Name of the file with identities list.')
        self.args = parser.parse_args(params)
        self.docs_pub_mngr = DocsPublishingManager(self.args)
        self.docs_pub_mngr.testDateStart = self.docs_pub_mngr.getTime()

    def start(self):
        timeoutTimer = None
        try:
            ok = True
            print(self.args.action)
            print(self.docs_pub_mngr.conf.pubs)
            timer = None
            if self.args.timeout:
                timeoutTimer = threading.Timer(self.args.timeout, self.docs_pub_mngr.doTimeout)
                timeoutTimer.start()
            if self.args.action == 'setup':
                ok = self.docs_pub_mngr.doPreparation(self.docs_pub_mngr.doSetup)
            elif self.args.action == 'categories':
                ok = self.docs_pub_mngr.doPreparation(self.docs_pub_mngr.doCategories)
            elif self.args.action == 'run':
                ok = self.docs_pub_mngr.doRun(self.args.private)
            elif self.args.action == 'noop':
                ok = True
            return ok

        except KeyboardInterrupt:
            self.docs_pub_mngr.testDateEnd = self.docs_pub_mngr.getTime()
            self.docs_pub_mngr.testTime = round(self.docs_pub_mngr.testDateEnd - self.docs_pub_mngr.testDateStart, self.docs_pub_mngr.ACC)
            self.docs_pub_mngr.stop = True
            self.docs_pub_mngr.generateReport(self.docs_pub_mngr.reportName, self.docs_pub_mngr.testTime, True)
            sys.exit(3)
        except Exception as e:
            print(e)
            sys.exit(2)
        finally:
            if timeoutTimer:
                timeoutTimer.cancel()
            if self.docs_pub_mngr.debug:
                for url, inProgress in self.docs_pub_mngr.publishers.items():
                    print(str(inProgress))



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
        elif conf.action == 'run_reading':
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