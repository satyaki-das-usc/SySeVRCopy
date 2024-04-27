import functools
import json
import logging
import os
import sys
from argparse import ArgumentParser
from multiprocessing import Manager, Pool, Queue, cpu_count
from os.path import exists, isdir, join
from typing import List, cast

from omegaconf import DictConfig, OmegaConf
from tqdm import tqdm

from src.data_generator import read_csv

USE_CPU = cpu_count()

ground_truth = dict()
data_folder = ""
dataset_root = ""
source_root_path = ""
csv_path = ""

def init_log():
    LOG_DIR = join(dataset_root, "logs")
    if not isdir(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    logging.basicConfig(
        handlers=[
            logging.FileHandler(join(LOG_DIR, "func_level_ground_truth.log")),
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

def process_file_parallel(cpp_path, queue: Queue):
    file_vul_lines = set()
    if cpp_path in ground_truth:
        file_vul_lines = set(ground_truth[cpp_path])
    
    SRC_PATH = join(source_root_path, cpp_path)
    with open(SRC_PATH, "r") as rfi:
        try:
            src_lines = rfi.readlines()
        except UnicodeDecodeError as e:
            print(SRC_PATH)
            sys.exit(0)
            
    nodes_dir = join(csv_path, cpp_path)
    joern_nodes = read_csv(join(nodes_dir, "nodes.csv"))
    function_nodes = [x for x in joern_nodes if x["type"] == "Function"]
    function_def_nodes = [(idx, x) for idx, x in enumerate(joern_nodes) if x["type"] == "FunctionDef"]
    assert(len(function_nodes) == len(function_def_nodes))
    function_metadata = []
    for func_node, func_def_idx_node in zip(function_nodes, function_def_nodes):
        start_idx, func_def_node = func_def_idx_node
        functionId = func_def_node["functionId"]
        func_start_line = int(func_node["location"].split(":")[0])
        func_linenums = [func_start_line]
        func_linenums.extend([int(x["location"].split(":")[0]) for x in joern_nodes[start_idx:] if ":" in x["location"] and x["functionId"] == functionId])
        func_linenums = sorted(set([x for x in func_linenums if x >= func_start_line]))
        func_end_line = func_linenums[-1]

        brace_matching = 0
        for line in src_lines[func_start_line - 1: func_end_line]:
            brace_matching += (line.count("{") - line.count("}"))
        end_line = func_end_line
        
        while brace_matching != 0:
            if end_line == len(src_lines):
                end_line == func_end_line
                break
            brace_matching += (src_lines[end_line].count("{") - src_lines[end_line].count("}"))
            end_line += 1
        if end_line == func_end_line:
            continue
        
        funclines = set(list(range(func_start_line, end_line + 1)))

        function_metadata.append({
            "functionId": functionId,
            "start_line": func_start_line,
            "end_line": end_line,
            "target": 1 if len(funclines.intersection(file_vul_lines)) != 0 else 0
        })
    
    return {cpp_path: function_metadata}

if __name__ == "__main__":
    __args = parse_args()
    config = cast(DictConfig, OmegaConf.load(__args.config))
    data_folder = config.data_folder
    dataset_root = join(data_folder, config.dataset.name)
    init_log()
    
    source_root_path = join(dataset_root, config.source_root_folder)
    csv_path = join(dataset_root, config.csv_folder)

    ground_truth_path = join(dataset_root, config.ground_truth_filename)
    logging.info(f"Reading ground truth from {ground_truth_path}...")
    with open(ground_truth_path, "r") as rfi:
        ground_truth = json.load(rfi)
    logging.info("Completed.")
    
    all_cpp_paths = []

    all_cpp_paths_filepath = join(dataset_root, config.cpp_paths_filename)

    if not exists(all_cpp_paths_filepath):
        logging.info(f"{all_cpp_paths_filepath} not found. Retriving all source code files from {source_root_path}...")
        for root, dirs, files in os.walk(source_root_path, topdown=True):
            for file_name in files:
                rel_dir = os.path.relpath(root, source_root_path)
                rel_file = join(rel_dir, file_name)
                if not rel_file.endswith(".c") and not rel_file.endswith(".cpp") and not rel_file.endswith(".h"):
                    continue
                all_cpp_paths.append(rel_file)
        logging.info(f"Successfully retrieved {len(all_cpp_paths)} files. Writing all cpp_paths to {all_cpp_paths_filepath}...")
        with open(all_cpp_paths_filepath, "w") as wfi:
            json.dump(all_cpp_paths, wfi)
    else:
        logging.info(f"Retrieving cpp filepaths from {all_cpp_paths_filepath}...")
        with open(all_cpp_paths_filepath, "r") as rfi:
            all_cpp_paths = json.load(rfi)
        logging.info("Completed.")
    
    logging.info(f"Going over {len(all_cpp_paths)} files...")
    with Manager() as m:
        message_queue = m.Queue()  # type: ignore
        pool = Pool(USE_CPU)
        process_func = functools.partial(process_file_parallel, queue=message_queue)
        function_metadata_list: List = [
            filename
            for filename in tqdm(
                pool.imap_unordered(process_func, all_cpp_paths),
                desc=f"Source files",
                total=len(all_cpp_paths),
            )
        ]

        message_queue.put("finished")
        pool.close()
        pool.join()

    function_metadata_dict = dict()
    logging.info(f"Converting data to JSON...")
    for entry in function_metadata_list:
        for key, value in entry.items():
            function_metadata_dict[key] = value

    function_metadata_path = join(dataset_root, config.function_metadata_filename)
    logging.info(f"Conversion completed. Writing function metadata to {function_metadata_path}...")
    with open(function_metadata_path, "w") as wfi:
        json.dump(function_metadata_dict, wfi, indent=2)
    logging.info("Completed.")