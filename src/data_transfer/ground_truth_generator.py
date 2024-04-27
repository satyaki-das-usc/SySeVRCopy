import json
import logging
import os
import xml.etree.ElementTree as ET
from argparse import ArgumentParser
from os.path import exists, isdir, join
from typing import Dict, List, Set, cast

from omegaconf import DictConfig, OmegaConf
from tqdm import tqdm

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
            logging.FileHandler(join(LOG_DIR, "ground_truth_generator.log")),
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

def getCodeIDtoPathDict(testcases: List) -> Dict[str, Dict[str, Set[int]]]:
    codeIDtoPath: Dict[str, Dict[str, Set[int]]] = {}
    for testcase in testcases:
        files = testcase.findall("file")
        testcaseid = testcase.attrib["id"]
        codeIDtoPath[testcaseid] = dict()

        for file in files:
            path = file.attrib["path"]
            flaws = file.findall("flaw")
            mixeds = file.findall("mixed")
            fix = file.findall("fix")
            VulLine = set()
            if (flaws != [] or mixeds != [] or fix != []):
                if (flaws != []):
                    for flaw in flaws:
                        VulLine.add(int(flaw.attrib["line"]))
                if (mixeds != []):
                    for mixed in mixeds:
                        VulLine.add(int(mixed.attrib["line"]))

            codeIDtoPath[testcaseid][path] = VulLine

    return codeIDtoPath

if __name__ == "__main__":
    __args = parse_args()
    config = cast(DictConfig, OmegaConf.load(__args.config))
    data_folder = config.data_folder
    dataset_root = join(data_folder, config.dataset.name)
    init_log()
    
    source_root_path = join(dataset_root, config.source_root_folder)
    csv_path = join(dataset_root, config.csv_folder)
    xml_path = join(dataset_root, config.xml_filename)
    tree = ET.ElementTree(file=xml_path)
    testcases = tree.findall("testcase")
    logging.info(f"Building code testcaseid to path map...")
    pathToCodeID = getCodeIDtoPathDict(testcases)
    logging.info(f"Completed.")

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


    logging.info(f"Generating ground truth...")
    ground_truth_set = dict()

    for test_ID, file_vul_lines in tqdm(pathToCodeID.items(), total=len(pathToCodeID)):
        for cpp_path, vul_lines in file_vul_lines.items():
            actual_vul_lines = [line for line in vul_lines if line > 0]
            if len(actual_vul_lines) == 0:
                continue
            if cpp_path not in all_cpp_paths:
                continue
            if cpp_path not in ground_truth_set:
                ground_truth_set[cpp_path] = set()
            ground_truth_set[cpp_path] = ground_truth_set[cpp_path].union(actual_vul_lines)

    ground_truth = {cpp_path: list(vul_lines) for cpp_path, vul_lines in ground_truth_set.items()}

    ground_truth_path = join(dataset_root, config.ground_truth_filename)
    logging.info(f"Ground truth generation completed. Wrting JSON to {ground_truth_path}...")
    with open(ground_truth_path, "w") as wfi:
        json.dump(ground_truth, wfi, indent=2)
    logging.info(f"Writing completed.")