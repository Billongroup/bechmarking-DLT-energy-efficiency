#!/usr/bin/env python3

import logging
import os.path

import time
import zeep
import requests


class Endpoint:
    """
    This is somewhat hackish class.
    It represents a single endpoint (ip:port) that implements single `.wsdl` file.
    Class automagically lists all methods from wsdl and dynamically creates class methods for them.
    Further, it classifies those methods based on name (but allows overriding automatic classification with ctor params):

    * all methods can be called synchronously (single request).
      They are created with name as-is (see run_synchro_method)
    * some methods are designed to be called repeatedly until they return OK (e.g. Setup).
      They are created with prefix repeat_ (see run_repeat_method)
    * quite a few methods are paired: Foo returns jobId, which is input for FooStatus which must be called untill it returns OK.
      They are created as single method with prefix wait_ (see run_synchro_method)
    """

    def __init__(self, wsdl, async_methods, repeat_methods, ip, port):
        self.wsdl = wsdl
        if not os.path.isfile(wsdl):
            wsdl = os.path.join(os.path.dirname(__file__), wsdl)  # fallback to wsdls in directory of this file
        self.history = zeep.plugins.HistoryPlugin(maxlen=100)
        self.client = zeep.Client(wsdl, transport=zeep.Transport(operation_timeout=15), plugins=[self.history])
        replacement = list(self.client.wsdl.bindings.keys())[0]
        self.addr = 'http://{ip}:{port}'.format(ip=ip, port=port)
        self.service = self.client.create_service(replacement, self.addr)
        self.log = logging.getLogger(os.path.basename(wsdl))

        # classify asynchronous methods (with jobId). Use hint if available
        synchro_methods, async_methods = Endpoint.classifyMethods(dir(self.service), async_methods)

        for method_name in synchro_methods:
            method = lambda *args, method_name=method_name: self.run_synchro_method(method_name, *args)
            setattr(self, method_name, method)
        for method_name, method_status_name in async_methods:
            # WTF lambda?
            # we create method which is pass-through to run_async_method.
            # *args are arguments that user will provide (actually single dictionary with request params)
            # timeout can be overridden by user but doesn't need to. It limits how long repetitions will last
            # rest of "arguments" are in fact like c++ lambda capture list
            method = lambda *args, timeout=None, method_name=method_name, method_status_name=method_status_name: \
                self.run_async_method(
                    method_name,
                    method_status_name,
                    *args,
                    timeout=timeout)
            setattr(self, 'wait_' + method_name, method)
            method2 = lambda *args, timeout=None, method_status_name=method_status_name: \
                self.run_repeat_method(method_status_name, *args, timeout=timeout)
            setattr(self, 'repeat_' + method_status_name, method2)
        for method_name in repeat_methods:
            method = lambda *args, timeout=None, method_name=method_name: \
                self.run_repeat_method(method_name, *args, timeout=timeout)
            setattr(self, 'repeat_' + method_name, method)

    def run_synchro_method(self, method_name, *args):
        self.log.debug('Synchro request {}'.format(method_name))
        try:
            return self.service[method_name](*args)
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(e)
        except requests.exceptions.Timeout as e:
            raise requests.exceptions.Timeout(e)

    def run_async_method(self, method_name, method_status_name, *args, timeout=None):
        try:
            ans1 = self.service[method_name](*args)
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(e)
        except requests.exceptions.Timeout as e:
            raise requests.exceptions.Timeout(e)
        jobId = ans1.jobId
        self.log.debug('Request {} started, jobId: {}'.format(method_name, jobId))
        newArgs = {'jobId': jobId}
        return self.run_repeat_method(method_status_name, newArgs, timeout=timeout)

    def run_repeat_method(self, method_name, *args, timeout=None):
        self.log.debug('Starting request {}'.format(method_name))
        if timeout is None:
            timeout = 15 * 60
        prevStatus = ''
        sleeper = Sleeper(totalTime=timeout)
        while True:
            try:
                ans = self.service[method_name](*args)
            except requests.exceptions.ConnectionError as e:
                raise ConnectionError(e)
            try:
                status = ans.status.status
            except AttributeError:
                status = ans.status

            self.log.debug(status)
            if status == prevStatus:
                print('.', end='', flush=True)
            else:
                if prevStatus:
                    print()  # newline
                prevStatus = status
                print(status, end='', flush=True)
                if status in ['PUBLISHING-OK', 'PUBLISHING-EXCEPTION', 'STATUS-INFO-NOT-FOUND', 'IDENTITY-CERTIFICATE-CREATION-PROBLEM',
                              'IDENTITY-NOT-FOUND', 'DUPLICATED-DOCUMENT-ALREADY-PUBLISHED'] \
                        or 'ERROR' in status:
                    print(flush=True)  # newline, because status is already printed but without newline
                    self.log.debug('Finished request {}. Final answer: {}'.format(method_name, ans))
                    return ans
            sleeper.sleep()

    @classmethod
    def classifyMethods(cls, allMethods, hint_async_methods):
        # ignore private methods
        allMethods = [method for method in allMethods if method[0] != '_']
        allMethods = sorted(allMethods)

        # treat all methods as synchro methods.
        # This way user can call async method's submethods manually
        ret_synrcho = list(allMethods)

        # For async methods use those provided by hint (but only once - we remove them from set of available)
        ret_async = list(hint_async_methods)
        unhandledMethods = list(allMethods)
        for a, b in hint_async_methods:
            unhandledMethods.remove(a)
            if b in unhandledMethods:
                # maybe was already removed: [(FooA, FooStatus), (FooB, FooStatus)]
                unhandledMethods.remove(b)

        # Among remaining methods search for those that havePair DoStuff, DoStuffStatus.
        # We use while-pop so that we can remove DoStuffStatus inside loop.
        # DoStuff will always pop before DoStuffStatus because list is sorted.
        while len(unhandledMethods) > 0:
            item = unhandledMethods.pop(0)
            if item + 'Status' in unhandledMethods:
                ret_async.append((item, item + 'Status'))
                unhandledMethods.remove(item + 'Status')

        return ret_synrcho, ret_async

    def __repr__(self):
        return '[{wsdl} @ {addr}]'.format(wsdl=self.wsdl, addr=self.addr)

    def debugLast(self):
        import lxml
        ret = lxml.etree.tostring(self.history.last_sent['envelope'], pretty_print=True).decode() + '\n\n' + lxml.etree.tostring(
            self.history.last_received['envelope'], pretty_print=True).decode()
        print(ret)
        return ret


class SupervisorEndpoint(Endpoint):
    def __init__(self, ip, port):
        Endpoint.__init__(self, 'SupervisorInterfaceService.wsdl',
                          async_methods=[],
                          repeat_methods=[],
                          ip=ip,
                          port=port)


class PublisherEndpoint(Endpoint):
    def __init__(self, ip, port):
        Endpoint.__init__(self, 'DMPtoCKKDurableMediaInterfaceService.wsdl',
                          async_methods=[('ForgetDocument', 'ForgetDocumentJobStatus'),
                                         ('PublishPublicDocument', 'GetPublishStatus'),
                                         ('PublishPrivateDocument', 'GetPublishStatus'),
                                         ('GetPrivateDocumentAuthorizationCode', 'GetPrivateDocumentAuthorizationCodeStatus')
                                         ],
                          repeat_methods=['Setup', 'GetDocument'],
                          ip=ip,
                          port=port)


class KycGwEndpoint(Endpoint):
    def __init__(self, ip, port):
        Endpoint.__init__(self, 'BillonKYCIdentityManagementInterfaceService.wsdl',
                          async_methods=[],
                          repeat_methods=[],
                          ip=ip,
                          port=port)


class ObserverEndpoint(Endpoint):
    def __init__(self, ip, port):
        Endpoint.__init__(self, 'ObserverInterfaceService.wsdl',
                          async_methods=[],
                          repeat_methods=[],
                          ip=ip,
                          port=port)


class CnodePrivateDocumentsEndpoint(Endpoint):
    def __init__(self, ip, port):
        Endpoint.__init__(self, 'PrivateDocumentsReadInterfaceService.wsdl',
                          async_methods=[],
                          repeat_methods=[],
                          ip=ip,
                          port=port)


class CnodePublicDocumentsEndpoint(Endpoint):
    def __init__(self, ip, port):
        Endpoint.__init__(self, 'PublicDocumentsReadInterfaceService.wsdl',
                          async_methods=[],
                          repeat_methods=[],
                          ip=ip,
                          port=port)


def isAnswerOk(answer):
    try:
        status = answer.status.status
    except AttributeError:
        status = answer.status
        # if it raises again, it is really a problem, so no second try-except
    return status == 'PUBLISHING-OK'


class Sleeper:
    def __init__(self, initTime=1, maxTime=20, totalTime=None):
        self.nextTime = initTime
        self.maxTime = maxTime
        self.totalTimeLeft = totalTime

    def sleep(self):
        if self.totalTimeLeft is not None and self.totalTimeLeft <= 0:
            raise TimeoutError()
        time.sleep(self.nextTime)
        if self.totalTimeLeft is not None:
            self.totalTimeLeft -= self.nextTime
        self.nextTime = min(self.maxTime, self.nextTime * 1.5)
        if self.totalTimeLeft is not None:
            self.nextTime = min(self.nextTime, self.totalTimeLeft)
        return self.canWait()

    def canWait(self):
        return self.totalTimeLeft is None or self.totalTimeLeft > 0

    def __iter__(self):
        """
        For use in `for hasNext in Sleep(totalTime=10):`
        Yields bool value True as long as there will be another sleep.
        On first iteration returns immediately (without sleep) either True or False (False is strange, means totalTime=0.
        Will sleep before every next item.
        On last iteration should return False and finish iteration
        """
        yield self.canWait()
        while self.canWait():
            yield self.sleep()


if __name__ == "__main__":
    supervisor = SupervisorEndpoint('10.0.20.180', 31400)
    publisher = PublisherEndpoint('10.0.20.180', 31404)
    kycgw = KycGwEndpoint('10.0.20.180', 31402)
    cnodePubl = CnodePublicDocumentsEndpoint('10.0.20.180', 31601)
    cnodePriv = CnodePrivateDocumentsEndpoint('10.0.20.180', 31602)

    print(kycgw.Hello())
    print(kycgw)
    predefinedLimits = supervisor.GetPredefinedLimits()
    print(predefinedLimits)
    addPublisherAns = supervisor.wait_AddPublisher({'name': 'Nazwa', 'clusterSize': 10})
    addPublisherAns = supervisor.wait_AddPublisher({'name': 'Nazwa', 'clusterSize': 10, 'kycGw': kycgw.Hello().kycGwId})
    print(addPublisherAns)
    print(publisher.repeat_Setup(timeout=30 * 60))
