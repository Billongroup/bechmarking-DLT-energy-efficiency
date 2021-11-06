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

import vui

class Bcolors:
    """ Colors for self.logging """
    OKGREEN = '\033[32m'
    WARNING = '\033[93m'
    ERROR = '\033[91m'
    FAILED = '\033[91m'
    ENDC = '\033[0m'


class TestData:
    def __init__(self, payer, receiver, amount, currency, test_id, short_description):
        self.payer = payer
        self.receiver = receiver
        self.amount = amount
        self.currency = currency
        self.test_id = test_id
        self.task_id = None
        self.end_time = None
        self.short_description = short_description
        self.start_time = None
        self.end_time = None


class TestP2PFast:
    def __init__(self, amount=900, currency='BIL', parallel_per_cnode=1,
                 min_delay=1, tr_per_cnode=1, max_concurrent_tr_per_cnode=None, short_description='tekst', success_level_percent=100, sleep=1,
                 num_of_instances=1, instance=1, wait_after_new_transaction=0.5):
        self.amount = amount
        self.currency = currency
        self.cnodes = self.findCnodes()
        self.processes = 0  # this is set below
        self.parallel_per_cnode = parallel_per_cnode
        self.min_delay = min_delay
        self.tr_per_cnode = tr_per_cnode
        self.max_concurrent_tr_per_cnode = max_concurrent_tr_per_cnode
        self.first_ended = None
        self.first_ended_success = None
        self.time_to_end = int(time.time()) + 60 * 15
        self.short_description = short_description
        self.success_level_percent = success_level_percent
        self.start_time = self.time_to_end
        self.end_time = 0
        self.average_time = 0
        self.sleep = sleep
        self.num_of_instances = num_of_instances
        self.instance = instance
        self.wait_after_new_transaction = wait_after_new_transaction
        self.name = 'TestP2PFast'
        self.ignoreFails = False
        self.success = 0
        self.failed = 0
        self.timeStart = None
        self.timeStop = None

    def findCnodes(self):
        nodes_file = os.path.join('nodes.yaml')
        if os.path.exists(nodes_file):
            with open(nodes_file, 'r', encoding='utf-8') as stream:
                data_loaded = yaml.safe_load(stream)
            return data_loaded
        return {}

    def makeProgress(self, transaction_number, success=True, prefix='Test result: '):
        if success:
            self.success += 1
        else:
            self.failed += 1

        sufix = ('Success: ' + str(self.success)).ljust(12) + (' Failed: ' + str(self.failed)).ljust(
            10) + ' Total: ' + str(transaction_number)
        self.borgUtils.print_progress(self.success, transaction_number, prefix=prefix, suffix=sufix, decimals=1,
                                      bar_length=100, failed=self.failed)

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
        iteration = success + failed
        bar_length = min(bar_length, shutil.get_terminal_size(fallback=(128, 128))[0] - len(prefix) - len(suffix) - 12)
        str_format = "{0:." + str(decimals) + "f}"
        percents = str_format.format(100 * (iteration / float(total)))
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

        if iteration == total:
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
        return len(self.cnodes) > 1

    def list_rot(self, data, shift):
        return data[shift:] + data[:shift]

    def merge_results(self, result):
        success_size, failed_size, min_start_time, max_end_time, sum_time = result
        for i in range(0, success_size):
            self.makeProgress(transaction_number=self.all_tests * self.tr_per_cnode, success=True)
        for i in range(0, failed_size):
            self.makeProgress(transaction_number=self.all_tests * self.tr_per_cnode, success=False)
        self.start_time = min(self.start_time, min_start_time)
        self.end_time = max(self.end_time, max_end_time)
        # here we only add -> number of transactions will be known at the end
        self.average_time += sum_time 

    def test_p2p(self, currency='BIL'):
        aux = list(self.cnodes)
        aux.sort(key=lambda a: a['user'])# To make sure we have the same order in all instances
        aux = chunk(aux, self.num_of_instances, self.instance) # Select subset of all CNODEs

        self.processes = int(len(aux)) * self.parallel_per_cnode
        self.all_tests = self.processes
        print('len(aux) ' + str(len(aux)) + ', self.min_delay ' + str(self.min_delay)
              + ', self.tr_per_cnode ' + str(self.tr_per_cnode)
              + ', self.max_concurrent_tr_per_cnode ' + str(self.max_concurrent_tr_per_cnode)
              + ', self.processes ' + str(self.all_tests)
              + ', instance ' + str(self.instance) + '/' + str(self.num_of_instances))

        futures = []

        r1 = random.randint(0, 1000000)  # * 1000

        self.logP2P('Lista self.cnodes: ' + str(self.cnodes))
        self.logP2P('Lista wybranych cnodow: ' + str(aux))

        result_path = os.path.dirname(os.path.realpath(__file__))
        part_res_path = os.path.join(result_path, 'transactions_summary')
        os.makedirs(part_res_path, exist_ok=True)
        tstart_time = int(time.time())
        with multiprocessing.Pool(self.all_tests) as pool:
            for i in range(0, self.processes):
                 futures.append(pool.apply_async(make_set_of_transactions, args=(aux, self.amount, currency, self.tr_per_cnode, self.max_concurrent_tr_per_cnode, self.min_delay, self.time_to_end, self.sleep, self.wait_after_new_transaction),
                    kwds={'test_id': str(i + r1), 'short_description': self.short_description},
                    callback=self.merge_results))
                
            pool.close()
            pool.join()
        tend_time = int(time.time())
        time_dur_msg = 'Duration of the test: ' + str(tend_time - tstart_time) + ' seconds'
        stats_msg = 'statistic p2p test: avg ' + format(self.success / (self.end_time - self.start_time), '.2f') + ' transactions per second.'

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
                        idk, status, beg_time, end_time, block_time,  payer, receiver, amount, currency = row
                        full_result[idk] = [status, beg_time, end_time, block_time, payer, receiver, amount, currency]
            else:
                continue

        result_path = os.path.join(result_path, 'final_result.csv')
        with open(result_path, 'w') as f:
            w = csv.writer(f, delimiter=' ')
            w.writerow(['ID', 'STATUS', 'START_TIME', 'END_TIME', 'BLOCK_TIME', 'PAYER', 'RECEIVER', 'AMOUNT', 'CURRENCY'])
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
def start_transaction(payer, receiver, amount, currency, test_id, short_description):
    start_time = int(1000 * time.time())
    test_data = TestData(payer, receiver, amount, currency, test_id, short_description)
    result, task_id = vui.give_money(
        test_data.payer.host + ':' + str(getattr(test_data.payer, 'payment-webservice-port')),
        test_data.receiver.user, test_data.amount, currency=test_data.currency, id_prefix=test_data.test_id)
    test_data.task_id = task_id
    if not result:
        test_data.start_time = start_time
        test_data.end_time = int(time.time())
        return False, test_data
    return True, test_data


def check_transaction(test_data):
    status = vui.get_task_status(
        test_data.payer.host + ':' + str(getattr(test_data.payer, 'payment-webservice-port')),
        test_data.task_id)
    if status.get('status', '').startswith('FINISH'):
        if status.get('status', '') == 'FINISHED_OK':
            additional_info = json.loads(status.get('additionalInfo', ''))
            if additional_info:
                time_format_start = '%Y-%m-%d %H:%M:%S'
                time_format_end = '%Y-%m-%d %H:%M:%S'
                start_time = int(datetime.strptime(additional_info['startDate'], time_format_start).timestamp())                                                                            
                end_time = int(datetime.strptime(additional_info['updateDate'], time_format_end).timestamp())
                block_time_ms = int(additional_info['blockTimeStamp'])
                s_t = datetime.fromtimestamp(start_time)
                e_t = datetime.fromtimestamp(end_time)
            return True, start_time, end_time, status.get('status', ''), s_t, e_t, block_time_ms
        else:
            return True, None, None, status.get('status', ''), None, None, None
    else:
        return False, None, None, None, None, None, None
        

def make_set_of_transactions(clients, amount, currency, how_many_tr, max_concurrent_tr, min_delay, time_to_end, sleep, wait_after_new_transaction=0.5, test_id='', short_description='tekst'):
    size = 0
    failed_size = 0
    current = []
    last_start = int(time.time())
    to_check = 0
    min_start_time = time_to_end
    max_end_time = 0
    time_sum = 0
    statuses = {}
    time_to_finish_first = int(time.time() + 60)
    while size + failed_size < how_many_tr:
        # check one
        if len(current) > 0:
            to_check += 1
            to_check = to_check % len(current)
            finished, start_time, end_time, status, s_t, e_t, block_time_ms = check_transaction(current[to_check])
            if finished:
                current_task = current[to_check]
                if status is not None and status == 'FINISHED_OK':
                    current_task = current[to_check]
                    min_start_time = min(start_time, min_start_time)
                    max_end_time = max(end_time, max_end_time)
                    statuses[current_task.task_id] = [status, s_t, e_t, block_time_ms,  current_task.payer.user, current_task.receiver.user, amount, currency]
                    current.pop(to_check)
                    size += 1
                    time_sum += (end_time - start_time)
                else:
                    statuses[current[to_check].task_id] = [status, 'NaN', 'NaN', 'NaN', current_task.payer.user, current_task.receiver.user, amount, currency]
                    current.pop(to_check)
                    failed_size +=1

        # start one of possible
        now = int(time.time())
        if size == 0 and now > time_to_finish_first:
            break
        if len(current) + size + failed_size < how_many_tr and (max_concurrent_tr is None or len(current) < max_concurrent_tr) and now - last_start > min_delay:
            for _ in range(len(current), max_concurrent_tr + 1):
                now = int(time.time())
                last_start = now
                r1 = random.randint(0, len(clients) - 1)
                r2 = random.randint(1, len(clients) - 1)
                payer = clients[r1]
                receiver = clients[(r1 + r2) % (len(clients) - 1)]
                result, test_data = start_transaction(payer, receiver, amount, currency, test_id, short_description)
                if not result:
                    failed_size += 1
                    pass
                else:
                    current.append(test_data)
                time.sleep(wait_after_new_transaction)

        time.sleep(sleep)
        if now > time_to_end:
            for cur in current:
                statuses[cur.task_id] = ['ERROR', 'NaN', 'NaN', 'Nan', cur.payer.user, cur.receiver.user, amount, currency]
                failed_size += 1
            break
    dumpStatuses(statuses, test_id)
    return size, failed_size, min_start_time, max_end_time, time_sum


def dumpStatuses(stat_dict, test_id):
    dir_path = os.path.dirname(os.path.realpath(__file__))
    dir_path = os.path.join(dir_path, 'transactions_summary')
    f_path = os.path.join(dir_path, str(test_id) + '.csv')
    with open(f_path, 'w+') as f:
        w = csv.writer(f, delimiter=' ')
        for k,v in stat_dict.items():
            w.writerow([k, *v])


def chunk(l, num_of_chunks, chunk_num):
    len_of_chunk, rest = divmod(len(l), num_of_chunks)
    if len_of_chunk % 2 == 1:
        len_of_chunk -= 1

    if chunk_num == num_of_chunks:
        return l[(chunk_num - 1) * len_of_chunk:]
    else:
        return l[(chunk_num - 1) * len_of_chunk:chunk_num * len_of_chunk]


if __name__ == "__main__":
    test_ = TestP2PFast(max_concurrent_tr_per_cnode=2)
    test_.runBool()
