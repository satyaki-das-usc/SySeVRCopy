import functools
import json
import logging
import os
from argparse import ArgumentParser
# from src.preprocess.symbolizer import clean_gadget
from multiprocessing import Manager, Pool, Queue, cpu_count
from os.path import isdir, join
from typing import List, cast

from omegaconf import DictConfig, OmegaConf
from tqdm import tqdm

USE_CPU = cpu_count()

data_folder = ""
dataset_root = ""
source_root_path = ""

cleaning_methods = []
function_metadata = dict()

replacement_dict = {
    "good": {
        "good": "unk",
        "Good": "Unk"
    },
    "bad": {
        "bad": "unk",
        "Bad": "Unk"
    }
}

reverse_replacement_dict = {
    "good": {
        "good": "unk2",
        "Good": "Unk2"
    },
    "bad": {
        "bad": "unk2",
        "Bad": "Unk2"
    }
}

def init_log():
    LOG_DIR = join(dataset_root, "logs")
    if not isdir(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    logging.basicConfig(
        handlers=[
            logging.FileHandler(join(LOG_DIR, "generate_dataset_for_transfer.log")),
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

def get_delabeled_processed_func(processed_func, label_text):
    rev_label_text = "bad" if label_text == "good" else "good"
    for key, value in replacement_dict[label_text].items():
        processed_func = processed_func.replace(key, value)
    for key, value in reverse_replacement_dict[rev_label_text].items():
        processed_func = processed_func.replace(key, value)
    
    return processed_func

def replace_leading_spaces_with_tabs(lines, tab_width=2):
    result = []

    for line in lines:
        leading_spaces = len(line) - len(line.lstrip())
        tabs = leading_spaces // tab_width
        spaces = leading_spaces % tab_width
        result.append("\t" * tabs + " " * spaces + line.lstrip())

    return result

def generate_entry_for_file_parallel(cpp_path, queue: Queue):
    SRC_PATH = join(source_root_path, cpp_path)

    with open(SRC_PATH, "r") as rfi:
        src_lines = rfi.readlines()

    functions = function_metadata[cpp_path]

    processed_functions = []

    for func in functions:
        label_text = "good" if func["target"] == 0 else "bad"
        line_nums = sorted(set(list(range(func["start_line"], func["end_line"] + 1))))
        raw_funclines = src_lines[func["start_line"] - 1: func["end_line"]]
        raw_func = "".join(raw_funclines)
        processed_func = "" + raw_func
        if "tab" in cleaning_methods:
            processed_funclines = replace_leading_spaces_with_tabs(raw_funclines)
            processed_func = "".join(processed_funclines)
        if "delabel" in cleaning_methods:
            processed_func = get_delabeled_processed_func(raw_func, label_text)
        # if "symbolize" in cleaning_methods:
        #     sym_code_lines = clean_gadget(raw_funclines)
        #     processed_func = "".join(sym_code_lines)
        processed_functions.append({
        "file_name": cpp_path,
        "line_nums": line_nums,
        "raw_func": raw_func,
        "func": processed_func,
        "target": func["target"],
        "commit_id": "",
        "project": ""
        })

    return processed_functions

if __name__ == "__main__":
    __args = parse_args()
    config = cast(DictConfig, OmegaConf.load(__args.config))
    data_folder = config.data_folder
    dataset_root = join(data_folder, config.dataset.name)
    init_log()
    
    source_root_path = join(dataset_root, config.source_root_folder)
    cleaning_methods = []

    function_metadata_path = join(dataset_root, config.function_metadata_filename)

    logging.info(f"Reading function metadata from {function_metadata_path}...")
    with open(function_metadata_path, "r") as rfi:
        function_metadata = json.load(rfi)
    logging.info(f"Completed.")
    
    all_filepaths = list(function_metadata.keys())
    cnt = len(all_filepaths)

    logging.info(f"Going over {cnt} files...")
    with Manager() as m:
        message_queue = m.Queue()  # type: ignore
        pool = Pool(USE_CPU)
        process_func = functools.partial(generate_entry_for_file_parallel, queue=message_queue)
        file_entries: List = [
            filename
            for filename in tqdm(
                pool.imap_unordered(process_func, all_filepaths),
                desc=f"Files",
                total=cnt,
            )
        ]

        message_queue.put("finished")
        pool.close()
        pool.join()

    flattened_file_entries = [x for entries in file_entries for x in entries]

    dataset_filepath = join(dataset_root, config.dataset_filename)
    logging.info(f"Completed. Writing dataset to {dataset_filepath}...")
    with open(dataset_filepath, "w") as wfi:
        json.dump(flattened_file_entries, wfi)
    logging.info(f"Writing Completed")