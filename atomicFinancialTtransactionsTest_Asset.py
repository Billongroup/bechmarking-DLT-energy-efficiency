import random
import time
import math
import concurrent.futures
import os
import multiprocessing
import json
from datetime import datetime
import csv
import yaml
import cnode
import shutil
import sys


class Bcolors:
    """ Colors for self.logging """
    OKGREEN = '\033[32m'
    WARNING = '\033[93m'
    ERROR = '\033[91m'
    FAILED = '\033[91m'
    ENDC = '\033[0m'


class TestData:
    def __init__(self, payer, receiver, test_id):
        self.test_id = test_id
        self.payer = payer
        self.receiver = receiver
        self.task_id = None
        self.end_time = None
        self.start_time = None
        self.status = None
        self.duration = None
        self.assets_requested = []
        self.finished = False


class TestP2PFast:
    def __init__(self, parallel_per_cnode=1,
                 min_delay=1, tr_per_cnode=1, max_concurrent_tr_per_cnode=10, short_description='tekst',
                 success_level_percent=100, sleep=1, test_duration=60 * 1,
                 num_of_instances=1, instance=1, wait_after_new_transaction=0.5):
        self.cnodes = self.findCnodes()
        self.processes = 0  # this is set below
        self.parallel_per_cnode = parallel_per_cnode
        self.min_delay = min_delay
        self.tr_per_cnode = tr_per_cnode
        self.max_concurrent_tr_per_cnode = max_concurrent_tr_per_cnode
        self.first_ended = None
        self.first_ended_success = None
        self.time_to_end = int(time.time()) + test_duration
        self.short_description = short_description
        self.success_level_percent = success_level_percent
        self.start_time = self.time_to_end
        self.end_time = 0
        self.average_time = 0
        self.sleep = sleep
        self.num_of_instances = num_of_instances
        self.instance = instance
        self.wait_after_new_transaction = wait_after_new_transaction
        self.name = 'TestAsset'
        self.ignoreFails = False
        self.success = 0
        self.failed = 0
        self.total = 0
        self.timeStart = None
        self.timeStop = None

    def findCnodes(self):
        nodes_file = os.path.join('nodes_cnodes.yaml')
        if os.path.exists(nodes_file):
            with open(nodes_file, 'r', encoding='utf-8') as stream:
                data_loaded = yaml.safe_load(stream)
            cnodes_data = []
            for node in data_loaded:
                if node == 'guard':
                    break
                if node['type'] == 'cnode':
                    cnodes_data.append(node)
            return cnodes_data
        return {}

    def makeProgress(self, transaction_number, success=True, prefix='Test result: '):
        if success:
            self.success += 1
        else:
            self.failed += 1

    def print_progress(self, success, total, prefix='', suffix='', decimals=1, bar_length=100, failed=0):
        """
        Call in a loop to create terminal progress bar
        @params:
            iteration   - Required  : current iteration (Int)
            total       - Required  : total iterations (Int)
            prefix      - Optional  : prefix string (Str)
            suffix      - Optional  : suffix string (Str)
            decimals    - Optional  : positive number of decimals in percent complete (Int)
            bar_length  - Optional  : character length of bar (Int)
        """
        if total == 0:
            print('Print progress error: total=0')
            return

        bar_length = min(bar_length, shutil.get_terminal_size(fallback=(128, 128))[0] - len(prefix) - len(suffix) - 12)
        str_format = "{0:." + str(decimals) + "f}"
        percents = str_format.format(100 * (success / float(total)))
        filled_length_success = int(round(bar_length * success / float(total)))
        filled_length_failed = int(round(bar_length * failed / float(total)))
        filled_length = filled_length_success + filled_length_failed

        if sys.getfilesystemencoding() == 'utf-8':
            progress_mark = '█'
        else:
            progress_mark = '#'

        progress_bar = Bcolors.OKGREEN + progress_mark * filled_length_success + Bcolors.FAILED + \
                       progress_mark * filled_length_failed + Bcolors.ENDC + '-' * (bar_length - filled_length)
        sys.stdout.write('\r%s |%s| %s%s %s' % (prefix, progress_bar, percents, '%', suffix))

        sys.stdout.write('\n')
        sys.stdout.flush()

    def runBool(self):
        return self.test_p2p()

    def logP2P(self, content):
        time_ = str(time.strftime('%y%m%d-%H:%M:%S.' + str(time.time() % 1)[2:5], time.gmtime()))
        with open('log_p2p_fast.txt', 'a') as log:
            log.write(time_ + ' ' + content)

    def shouldStart(self):
        self.logP2P('Nodes number: ' + str(len(self.cnodes)))
        return len(self.cnodes) > 2

    def list_rot(self, data, shift):
        return data[shift:] + data[:shift]

    def merge_results(self, result):
        success_size, failed_size, min_start_time, max_end_time, sum_time = result
        self.success += success_size
        self.failed += failed_size
        self.total = self.success + self.failed
        self.start_time = min(self.start_time, min_start_time)
        self.end_time = max(self.end_time, max_end_time)
        # here we only add -> number of transactions will be known at the end
        self.average_time += sum_time

    def test_p2p(self):
        debug = False
        aux = list(self.cnodes)
        aux.sort(key=lambda a: a['user'])  # To make sure we have the same order in all instances
        aux = chunk(aux, self.num_of_instances, self.instance)  # Select subset of all CNODEs

        self.processes = int(len(aux)) * self.parallel_per_cnode
        self.all_tests = self.processes

        test_desc = 'len(aux) ' + str(len(aux)) + ', self.min_delay ' + str(self.min_delay) + ', self.tr_per_cnode ' + \
                    str(self.tr_per_cnode) + ', self.max_concurrent_tr_per_cnode ' + \
                    str(self.max_concurrent_tr_per_cnode) + ', self.processes ' + str(self.all_tests) + ', instance ' + \
                    str(self.instance) + '/' + str(self.num_of_instances)
        print(test_desc)

        futures = []

        r1 = random.randint(0, 1000000)  # * 1000

        self.logP2P('Lista self.cnodes: ' + str(self.cnodes))
        self.logP2P('Lista wybranych cnodow: ' + str(aux))

        result_path = os.path.dirname(os.path.realpath(__file__))
        part_res_path = os.path.join(result_path, 'transactions_summary')
        os.makedirs(part_res_path, exist_ok=True)
        tstart_time = int(time.time())
        assets_start = []
        if debug:
            for c in aux:
                assets = cnode.get_assets(c['host'] + ':' + str(c['rest-webservice-port']),
                                          status=['WAITING'])
                assets_start += map(lambda a: a['assetId'], assets)

        with multiprocessing.Pool(self.all_tests) as pool:
            for i in range(0, self.processes):
                futures.append(pool.apply_async(make_set_of_transactions, args=(
                    aux, i % self.processes, self.max_concurrent_tr_per_cnode, self.min_delay,
                    self.time_to_end, self.sleep, self.wait_after_new_transaction),
                                                kwds={'test_id': str(i + r1)},
                                                callback=self.merge_results))
            pool.close()
            pool.join()
        sufix = ('Success: ' + str(self.success)).ljust(12) + (' Failed: ' + str(self.failed)).ljust(
            10) + ' Total: ' + str(self.total)
        self.print_progress(self.success, self.total, suffix=sufix, decimals=1, bar_length=100, failed=self.failed)
        tend_time = int(time.time())
        duration = tend_time - tstart_time
        time_dur_msg = 'Duration of the test: ' + str(duration) + ' seconds'
        stats_msg = 'statistic p2p test: avg ' + format(self.success / duration,
                                                        '.2f') + ' transactions per second.'

        assets_end = []
        time.sleep(10)
        if debug:
            for c in aux:
                assets = cnode.get_assets(c['host'] + ':' + str(c['rest-webservice-port']), status=['WAITING'])
                assets_end += map(lambda a: a['assetId'], assets)
        print('_____________________________________________________________________________________________________')
        print(test_desc)
        print('Count of assets before test: ' + str(len(assets_start)))
        print('Count of assets after test: ' + str(len(assets_end)))
        print('_____________________________________________________________________________________________________')
        if len(assets_start) > len(assets_end):
            assets_end.sort()
            assets_start.sort()
            print('WE HAVE A PROBLEM: asset count: ' + str(len(assets_start)) + '(start) vs ' + str(
                len(assets_end)) + '(end)')
            print(set(assets_start) - set(assets_end))
        print(time_dur_msg)
        self.logP2P(time_dur_msg)
        print(stats_msg)
        self.logP2P(stats_msg)

        if self.success:
            avg_time_msg = 'statistic p2p test: avg time tr: ' + format(self.average_time / self.success, '.2f')
            print(avg_time_msg)
            self.logP2P(avg_time_msg)
        else:
            avg_time_msg = 0

        self.logP2P('Merging partial results to csv')
        print('Merging partial results to csv')
        dir_path = os.path.dirname(os.path.realpath(__file__))
        dir_path = os.path.join(dir_path, 'transactions_summary')
        full_result = {}
        for filename in os.listdir(dir_path):
            if filename.endswith(".csv"):
                with open(os.path.join(dir_path, filename), 'r') as part_result:
                    reader = csv.reader(part_result, delimiter=' ')
                    for row in reader:
                        task_id, status, start_time, end_time, duration, sender, receiver, asset_requested = row
                        full_result[task_id] = [status, start_time, end_time, duration, sender, receiver,
                                                asset_requested]
            else:
                continue

        result_path = os.path.join(result_path, 'final_result.csv')
        with open(result_path, 'w') as f:
            w = csv.writer(f, delimiter=' ')
            w.writerow(
                ['task_id', 'status', 'start_time', 'end_time', 'duration', 'sender', 'receiver', 'asset_requested'])
            for k, v in full_result.items():
                w.writerow([k, *v])
            w.writerow([time_dur_msg])
            w.writerow([stats_msg])
            w.writerow([avg_time_msg])

        self.logP2P('Results in file: final_result.csv')
        print('Results in file: final_result.csv')
        return self.failed <= self.success * (100 - self.success_level_percent) / 100  # and balances_correct


"""
Here we use free functions, because there are problems with
pickling self between processes
"""


def logIds(content, payer):
    time_ = str(time.strftime('%y%m%d-%H:%M:%S.' + str(time.time() % 1)[2:5], time.gmtime()))
    with open('log_p2p_idst_' + payer + '.txt', 'a') as log:
        log.write(time_ + ' ' + content + '\n')


def start_asset_transaction(payer, receiver, test_id, asset_ids):
    start_time = int(1000 * time.time())
    test_data = TestData(payer, receiver, test_id)
    test_data.assets_requested = asset_ids
    response = cnode.send_assets(
        test_data.payer['host'] + ':' + str(test_data.payer['rest-webservice-port']), asset_ids,
        test_data.receiver['user'])
    test_data.start_time = start_time
    if 'taskId' not in response:
        test_data.end_time = int(time.time() * 1000)
        test_data.status = response['status']
        print('(' + test_data.payer['host'] + ':' + str(
            test_data.payer['rest-webservice-port']) + ') nie udalo sie wystartowac(' + str(
            asset_ids) + ') ' + test_data.status)

        return False, test_data
    test_data.task_id = response['taskId']
    # logIds('Started transaction: ' + test_data.task_id + ' ' + str(asset_ids), payer['user'])
    return True, test_data


def check_transaction(test_data):
    status_res = cnode.get_task_status(
        test_data.payer['host'] + ':' + str(test_data.payer['rest-webservice-port']),
        test_data.task_id)
    status = status_res['status'] if 'status' in status_res else ''
    if status.startswith('FINISH'):
        end_time = int(1000 * time.time())
        return True, [status, time.strftime('%Y-%m-%d, %H:%M:%S', time.gmtime(test_data.start_time / 1000)),
                      time.strftime('%m/%d/%Y, %H:%M:%S', time.gmtime(end_time / 1000)),
                      end_time - test_data.start_time, test_data.payer['user'],
                      test_data.receiver['user'], str(test_data.assets_requested)]
    else:
        return False, []


def make_set_of_transactions(clients, payer_id, max_concurrent_tr, min_delay, time_to_end, sleep,
                             wait_after_new_transaction=0.5, test_id='', asset_number=1):
    size = 0
    failed_size = 0
    current = []
    min_start_time = time_to_end
    max_end_time = 0
    time_sum = 0
    statuses = {}
    time_to_finish_first = int(time.time() + 60)
    asset_lists = {}
    last_asset_send = set()
    try:
        while time.time() < time_to_end:
            # check all
            for curr in current:
                finished, task_row = check_transaction(curr)
                if finished:
                    statuses[curr.task_id] = task_row
                    if task_row[0] is not None and task_row[0] == 'FINISHED_OK':
                        size += 1
                        time_sum += task_row[3]
                    else:
                        print('asset transfer form ' + curr.payer['user'] + '(' + curr.payer['host'] + ':' + str(
                            curr.payer['rest-webservice-port']) + ') of asset:' + str(
                            curr.assets_requested) + ' with status:' + task_row[0])
                        failed_size += 1
                    curr.finished = True
            current = list(filter(lambda cur: cur.finished is not True, current))
            # start one of possible
            now = int(time.time())
            if size == 0 and now > time_to_finish_first:
                break
            last_start = 0
            if (max_concurrent_tr is None or len(current) < max_concurrent_tr) and now - last_start > min_delay:
                for _ in range(len(current), max_concurrent_tr + 1):
                    now = int(time.time())
                    last_start = now
                    r2 = random.randint(1, len(clients) - 1)
                    payer = clients[payer_id]
                    receiver = clients[(payer_id + r2) % (len(clients))]
                    if payer['user'] not in asset_lists or len(asset_lists[payer['user']]) < asset_number:
                        assets = set(map(lambda a: a['assetId'],
                                          cnode.get_assets(payer['host'] + ':' + str(payer['rest-webservice-port']),
                                                           status=['WAITING'])))
                        if last_asset_send.intersection(assets):
                            assets = set(filter(lambda a: a not in last_asset_send, assets))

                        if len(assets) < asset_number:
                            print('not enough assets on cnode ' + payer['host'] + ':' + str(payer['rest-webservice-port']))
                            time.sleep(5)
                            break
                    else:
                        assets = asset_lists[payer['user']]
                    assets_to_send = set([assets.pop() for _ in range(asset_number)])
                    if set(assets_to_send).intersection(last_asset_send):
                        print('wtf we picked the same asset again! ' + str(assets_to_send))
                    result, test_data = start_asset_transaction(payer, receiver, test_id, list(assets_to_send))
                    asset_lists[payer['user']] = assets
                    if not result:
                        # failed_size += 1
                        pass
                    else:
                        last_asset_send = assets_to_send
                        current.append(test_data)
                    time.sleep(wait_after_new_transaction)
            time.sleep(sleep)
        dumpStatuses(statuses, test_id)
    except Exception as err:
        print(str(err))
    return size, failed_size, min_start_time, max_end_time, time_sum


def dumpStatuses(stat_dict, test_id):
    dir_path = os.path.dirname(os.path.realpath(__file__))
    dir_path = os.path.join(dir_path, 'transactions_summary')
    f_path = os.path.join(dir_path, str(test_id) + '.csv')
    with open(f_path, 'w+') as f:
        w = csv.writer(f, delimiter=' ')
        for task_id, values in stat_dict.items():
            w.writerow([task_id, *values])


def chunk(l, num_of_chunks, chunk_num):
    len_of_chunk, rest = divmod(len(l), num_of_chunks)
    if len_of_chunk % 2 == 1:
        len_of_chunk -= 1

    if chunk_num == num_of_chunks:
        return l[(chunk_num - 1) * len_of_chunk:]
    else:
        return l[(chunk_num - 1) * len_of_chunk:chunk_num * len_of_chunk]


if __name__ == "__main__":
    dir_path = os.path.dirname(os.path.realpath(__file__))
    dir_path = os.path.join(dir_path, 'transactions_summary')
    if os.path.exists(dir_path):
        shutil.rmtree(dir_path)
    test_ = TestP2PFast(max_concurrent_tr_per_cnode=2, test_duration=60)
    test_.runBool()
