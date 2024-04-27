import logging
import pandas as pd
import os
import json
import functools
from argparse import ArgumentParser

from omegaconf import OmegaConf, DictConfig
from multiprocessing import cpu_count, Manager, Pool, Queue

from typing import cast, List
from tqdm import tqdm
from os.path import isfile, isdir, join, exists
from src.data_generator import read_csv
from math import ceil

USE_CPU = cpu_count()
data_folder = ""
dataset_root = ""
dataset = []

def init_log():
    LOG_DIR = join(dataset_root, "logs")
    if not isdir(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    logging.basicConfig(
        handlers=[
            logging.FileHandler(join(LOG_DIR, "pair_generator.log")),
            logging.StreamHandler()
        ],
        format='%(asctime)s %(levelname)-8s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S')
    logging.info("=========New session=========")
    logging.info(f"Logging dir: {LOG_DIR}")

def parse_args():
    arg_parser = ArgumentParser()
    arg_parser.add_argument("-c",
                            "--config",
                            help="Path to YAML configuration file",
                            default="configs/dwk.yaml",
                            type=str)
    
    args = arg_parser.parse_args()

    return args

def process_data_parallel(entry_info, queue: Queue):
    idx, entry = entry_info
    if entry["target"] == 0:
        return {"idx": idx, "pairs": {}}
    file_name = entry["file_name"]
    same_file_samples = [(i, data) for i, data in enumerate(dataset) if data["file_name"] == file_name]
    vv_samples = []
    vn_samples = []
    for i, data in same_file_samples:
        if idx == i:
            continue
        if data["target"] == 1:
            vv_samples.append(i)
        else:
            vn_samples.append(i)
    
    return {"idx": idx, "pairs": {"vv": vv_samples, "vn": vn_samples}}

if __name__ == "__main__":
    __args = parse_args()
    config = cast(DictConfig, OmegaConf.load(__args.config))
    data_folder = config.data_folder
    dataset_root = join(data_folder, config.dataset.name)
    init_log()

    dataset_filepath = join(dataset_root, config.dataset_filename)

    logging.info(f"Reading dataset from {dataset_filepath}...")
    with open(dataset_filepath, "r") as rfi:
        dataset = json.load(rfi)
    logging.info(f"Reading completed.")

    cnt = len(dataset)
    logging.info(f"Going over {cnt} entries...")
    with Manager() as m:
        message_queue = m.Queue()  # type: ignore
        pool = Pool(USE_CPU)
        process_func = functools.partial(process_data_parallel, queue=message_queue)
        vv_vn_pairs: List = [
            filename
            for filename in tqdm(
                pool.imap_unordered(process_func, enumerate(dataset)),
                desc=f"Dataset entries",
                total=cnt,
            )
        ]

        message_queue.put("finished")
        pool.close()
        pool.join()
    
    logging.info(f"Pair Generation completed. Filtering nonvul pairs...")
    filtered_pairs = [entry for entry in vv_vn_pairs if len(entry["pairs"]) > 0]

    pairs_filepath = join(dataset_root, config.pairs_filename)
    logging.info(f"Filtering completed. Writing to {pairs_filepath}...")
    with open(pairs_filepath, "w") as wfi:
        json.dump(filtered_pairs, wfi, indent=2)
    logging.info(f"Writing completed.")