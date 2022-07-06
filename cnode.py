import requests
import time
def get_assets(url, ids=None, status=None, bankId=None, startTimeFrom=None, startTimeTo=None, updateTimeFrom=None, updateTimeTo=None):
    """
    :param url
    :param ids
    :param status
    :param bankId
    :param startTimeFrom
    :param startTimeTo
    :param updateTimeFrom
    :param updateTimeTo
    :return
        json array of assets
    """
    query = ''
    if ids:
        query += '?assetIds=' + str(ids)[2:-2].replace(' ', ''). replace('\'', '')
    if status:
        query += '&' if len(query) > 0 else '?'
        query += 'status=' + str(status)[2:-2].replace(' ', ''). replace('\'', '')
    if bankId:
        query += '&' if len(query) > 0 else '?'
        query += 'bankId=' + bankId
    if startTimeFrom:
        query += '&' if len(query) > 0 else '?'
        query += 'startTimeFrom=' + startTimeFrom
    if startTimeTo:
        query += '&' if len(query) > 0 else '?'
        query += 'startTimeTo=' + startTimeTo
    if updateTimeFrom:
        query += '&' if len(query) > 0 else '?'
        query += 'updateTimeFrom=' + updateTimeFrom
    if updateTimeTo:
        query += '&' if len(query) > 0 else '?'
        query += 'updateTimeTo=' + updateTimeTo

    url = 'http://' + url + '/asset' + query
    try:
        response = requests.get(url, timeout=60)
        if response.json()['status'] != 'SUCCESS':
            return []
        return response.json()['assetDataList']
    except requests.exceptions.ConnectionError as err:
        print('Error: ' + str(err))
        return []
    except Exception as err:
        print('Error2: ' + str(err))
        return []

def send_assets(url, ids, receiverName):
    """
    :param url
    :param ids
    :param receiverName
    :return
        json with status of transfer task
    """
    url = 'http://' + url + '/asset/send'
    try:
        data = {
            "username": receiverName,
            "assetIds": ids,
            "comment": ''
        }
        response = requests.post(url, json=data, timeout=60)
        return response.json()
    except requests.exceptions.ConnectionError as err:
        print(str(err))
        return False

def get_task_status(url, taskId):
    url = 'http://' + url + '/task?id='+taskId
    try:
        response = requests.get(url, timeout=60)
        return response.json()
    except requests.exceptions.ConnectionError as err:
        print(str(err))
        return False

def get_history(url, onlyTransactions=True, includeHidden=False, checkMonexes=False, fromDate=None, toDate=None):
    query = '?'
    query += 'onlyTransactions='+str(onlyTransactions)
    query += '&includeHidden='+str(includeHidden)
    query += '&checkMonexes='+str(checkMonexes)
    if fromDate:
        query += '&from='+fromDate
    if toDate:
        query += '&to='+toDate

    url = 'http://' + url + '/asset' + query
    try:
        response = requests.get(url, timeout=60)
        return response.json()
    except requests.exceptions.ConnectionError as err:
        print(str(err))
        return False

def transfer_assets(url, ids, receiverName):
    sendResponse = send_assets(url, ids, receiverName)
    if not sendResponse or 'taskId' not in sendResponse:
        print('problem with sendResponse: ' + sendResponse)
        return False
    taskId = sendResponse['taskId']
    status = ''
    while 'FINISHED' not in status:
        time.sleep(1)
        taskResponse = get_task_status(url, taskId)
        if not taskResponse or 'taskId' not in sendResponse:
            print(taskResponse)
            return False
        status = taskResponse['status']
    if status == 'FINISHED_OK':
        return True
    return False

def read_history_by_id(url, taskId):
    historyResponse = get_history(url)
    if not historyResponse or not hasattr(historyResponse, 'history'):
        return False
    history = historyResponse['history']['history']
    for record in history:
        if record['TransferId'] == taskId:
            return record
    return False

