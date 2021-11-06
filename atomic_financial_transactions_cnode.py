import requests
import requests
import xml.etree.ElementTree as ET
import time
import json
import random

headers = {'Content-Type': 'text/xml', 'charset': 'UTF-8'}

hello_soap = """
<soapenv:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="ns1">
   <soapenv:Header/>
   <soapenv:Body>
      <ns1:hello soapenv:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"/>
   </soapenv:Body>
</soapenv:Envelope>
"""

getMoneyStatus_soap = """<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope
  xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/"
  xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  xmlns:ns="ns1">
 <SOAP-ENV:Body  SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <ns:getMoneyStatus>
  </ns:getMoneyStatus>
 </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
"""

giveMoney_soap = """<soapenv:Envelope xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\" xmlns:xsd=\"http://www.w3.org/2001/XMLSchema\" xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" xmlns:ns1=\"ns1\">
   <soapenv:Header/>
   <soapenv:Body>
      <ns1:giveMoneyRequest soapenv:encodingStyle=\"http://schemas.xmlsoap.org/soap/encoding/\">
         <username xsi:type=\"xsd:string\">XreceiverX</username>
         <uniqueTransferId xsi:type=\"xsd:string\">XuniqueTransferIdX</uniqueTransferId>
         <type xsi:type=\"xsd:string\">GIVE_MONEY</type>
         <seriesId xsi:type=\"xsd:string\"></seriesId>
         <amount xsi:type=\"ns1:billonAmount\">
            <amount xsi:type=\"xsd:unsignedLong\">XamountX</amount>
            <currency xsi:type=\"xsd:string\">XcurrencyX</currency>
            <colour xsi:type=\"xsd:unsignedLong\">0</colour>
         </amount>
         <shortDescription xsi:type=\"xsd:string\">testy</shortDescription>
         <description xsi:type=\"xsd:string\"></description>
      </ns1:giveMoneyRequest>
   </soapenv:Body>
</soapenv:Envelope>
"""

getTaskStatus = """<soapenv:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="ns1">
   <soapenv:Header/>
   <soapenv:Body>
      <ns1:getTaskStatus soapenv:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
         <taskId xsi:type="xsd:string">XtaskIdX</taskId>
         <uniqueTransferId xsi:type="xsd:string"></uniqueTransferId>
         <callback xsi:type="xsd:boolean">false</callback>
      </ns1:getTaskStatus>
   </soapenv:Body>
</soapenv:Envelope>
"""

def get_money_status(url, currency='BIL', balance_type='freeBalance'):
    """
    :param url:
    :param currency:
    :param balance_type:
    :return:
    """
    url = 'http://' + url
    try:
        response = requests.post(url, data=getMoneyStatus_soap, headers=headers)
        root = ET.fromstring(response.content)
        requestStatus = next(root.iter('requestStatus')).text
        if requestStatus != 'SUCCESS':
            print('Zapytanie get_money_status zakonczylo sie statusem ' + requestStatus)
            return 0
        for free in root.iter(balance_type):
            if free.find('currency') is not None and free.find('currency').text == currency:
                if free.find('amount') is not None:
                    return int(free.find('amount').text)
        return 0
    except requests.exceptions.ConnectionError as err:
        print(str(err))
        return 0


def get_task_status(url, task_id):
    """
    :param url:
    :param task_id:
    :return:
    """
    url = 'http://' + url
    request = getTaskStatus.replace("XtaskIdX", task_id)
    try:
        res = {}
        response = requests.post(url, data=request, headers=headers, timeout=30)
        if False:
            print('url: ' + url)
            print(response.content)
        root = ET.fromstring(response.content)
        res['additionalInfo'] = next(root.iter('additionalInfo')).text
        res['progressPercent'] = next(root.iter('progressPercent')).text
        res['requestStatus'] = next(root.iter('requestStatus')).text
        if res['requestStatus'] == 'FINISHED_ERR':
            res['error_name'] = next(root.iter('error_name')).text
            print(res['error_name'])
        if res['requestStatus'] != 'SUCCESS':
            return {}
        res['status'] = next(root.iter('status')).text
        if res['status'] == 'FINISHED_ERR':
            res['error_name'] = json.loads(next(root.iter('additionalInfo')).text).get('error_name', 'UNKNOWN_ERROR')
        return res
    except requests.exceptions.ConnectionError as err:
        print(str(err))
        return {}


def give_money(url, receiver, amount, currency='BIL', check_status=False, id_prefix=''):
    """
    :param url:
    :param receiver:
    :param amount:
    :param currency:
    :param check_status:
    :return:
    """
    url = 'http://' + url
    try:
        give_money_ = giveMoney_soap.replace("XamountX", str(amount)).replace("XcurrencyX", currency).replace(
            'XreceiverX', receiver).replace('XuniqueTransferIdX', id_prefix + str(int(time.time())) + str(random.randint(0, 9999999999999)))
        print('wysylam ' + str(give_money_))
        response = requests.post(url, data=give_money_, headers=headers, timeout=30)
        root = ET.fromstring(response.content)
        requestStatus = next(root.iter('requestStatus')).text
        if requestStatus != 'SUCCESS':
            return False, requestStatus
        taskId = next(root.iter('taskId')).text
        return True, taskId
    except requests.exceptions.ConnectionError as err:
        print(str(err))
        return False, None
    except Exception as e:
        print(str(e))
        return False, None


def hello(url):
    """
    :param url:
    :return:
    """
    url = 'http://' + url

    try:
        response = requests.post(url, data=hello_soap, headers=headers)
        root = ET.fromstring(response.content)
        res = {}
        res['status'] = next(root.iter('status')).text
        res['pid'] = next(root.iter('pid')).text
        res['username'] = next(root.iter('username')).text
        return res
    except requests.exceptions.ConnectionError as err:
        print(str(err))
        return {}

