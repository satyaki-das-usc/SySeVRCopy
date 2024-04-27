import json
import xml.etree.ElementTree as ET
import networkx as nx
from typing import List, Set, Tuple, Dict
from os.path import join, exists, isfile
from argparse import ArgumentParser
import os
from tqdm import tqdm
from typing import cast
import dataclasses
from omegaconf import OmegaConf, DictConfig
from multiprocessing import cpu_count, Manager, Pool, Queue
import functools

USE_CPU =  2


def extract_line_number(idx: int, nodes: List) -> int:
    """
    return the line number of node index

    Args:
        idx (int): node index
        nodes (List)
    Returns: line number of node idx
    """
    while idx >= 0:
        c_node = nodes[idx]
        if 'location' in c_node.keys():
            location = c_node['location']
            if location.strip() != '':
                try:
                    ln = int(location.split(':')[0])
                    return ln
                except Exception as e:
                    print(e)
                    pass
        idx -= 1
    return -1


def read_csv(csv_file_path: str) -> List:
    """
    read csv file
    """
    assert exists(csv_file_path), f"no {csv_file_path}"
    data = []
    with open(csv_file_path) as fp:
        header = fp.readline()
        header = header.strip()
        h_parts = [hp.strip() for hp in header.split('\t')]
        for line in fp:
            line = line.strip()
            instance = {}
            lparts = line.split('\t')
            for i, hp in enumerate(h_parts):
                if i < len(lparts):
                    content = lparts[i].strip()
                else:
                    content = ''
                instance[hp] = content
            data.append(instance)
        return data


def extract_nodes_with_location_info(nodes):
    """
    Will return an array identifying the indices of those nodes in nodes array
    another array identifying the node_id of those nodes
    another array indicating the line numbers
    all 3 return arrays should have same length indicating 1-to-1 matching.
    
    """

    node_indices = []
    node_ids = []
    line_numbers = []
    node_id_to_line_number = {}
    for node_index, node in enumerate(nodes):
        assert isinstance(node, dict)
        if 'location' in node.keys():
            location = node['location']
            if location == '':
                continue
            line_num = int(location.split(':')[0])
            node_id = node['key'].strip()
            node_indices.append(node_index)
            node_ids.append(node_id)
            line_numbers.append(line_num)
            node_id_to_line_number[node_id] = line_num
    return node_indices, node_ids, line_numbers, node_id_to_line_number


def build_PDG(code_path: str, sensi_api_path: str,
              source_path: str) -> Tuple[nx.DiGraph, Dict[str, Set[int]]]:
    """
    build program dependence graph from code

    Args:
        code_path (str): source code root path
        sensi_api_path (str): path to sensitive apis
        source_path (str): source file path

    Returns: (PDG, key line map)
    """
    nodes_path = join(code_path, "nodes.csv")
    edges_path = join(code_path, "edges.csv")
    assert exists(sensi_api_path), f"{sensi_api_path} not exists!"
    with open(sensi_api_path, "r", encoding="utf-8") as f:
        sensi_api_set = set([api.strip() for api in f.read().split(",")])
    if not exists(nodes_path) or not exists(edges_path):
        print(nodes_path)
        print('here')
        return None, None
    nodes = read_csv(nodes_path)
    edges = read_csv(edges_path)
    call_lines = set()
    array_lines = set()
    ptr_lines = set()
    arithmatic_lines = set()
    if len(nodes) == 0:
        return None, None
    for node_idx, node in enumerate(nodes):
        ntype = node['type'].strip()
        if ntype == 'CallExpression':
            function_name = nodes[node_idx + 1]['code']
            if function_name is None or function_name.strip() == '':
                continue
            if function_name.strip() in sensi_api_set:
                line_no = extract_line_number(node_idx, nodes)
                if line_no > 0:
                    call_lines.add(line_no)
        elif ntype == 'ArrayIndexing':
            line_no = extract_line_number(node_idx, nodes)
            if line_no > 0:
                array_lines.add(line_no)
        elif ntype == 'PtrMemberAccess':
            line_no = extract_line_number(node_idx, nodes)
            if line_no > 0:
                ptr_lines.add(line_no)
        elif node['operator'].strip() in ['+', '-', '*', '/']:
            line_no = extract_line_number(node_idx, nodes)
            if line_no > 0:
                arithmatic_lines.add(line_no)

    PDG = nx.DiGraph(file_paths=[source_path])
    control_edges, data_edges = list(), list()
    node_indices, node_ids, line_numbers, node_id_to_ln = extract_nodes_with_location_info(
        nodes)
    for edge in edges:
        edge_type = edge['type'].strip()
        if True:  # edge_type in ['IS_AST_PARENT', 'FLOWS_TO']:
            start_node_id = edge['start'].strip()
            end_node_id = edge['end'].strip()
            if start_node_id not in node_id_to_ln.keys(
            ) or end_node_id not in node_id_to_ln.keys():
                continue
            start_ln = node_id_to_ln[start_node_id]
            end_ln = node_id_to_ln[end_node_id]
            if edge_type == 'CONTROLS':  # Control
                control_edges.append((start_ln, end_ln, {"c/d": "c"}))
            if edge_type == 'REACHES':  # Data
                data_edges.append((start_ln, end_ln, {"c/d": "d"}))
    PDG.add_edges_from(control_edges)
    PDG.add_edges_from(data_edges)
    return PDG, {
        "call": call_lines,
        "array": array_lines,
        "ptr": ptr_lines,
        "arith": arithmatic_lines
    }


def build_XFG(PDG: nx.DiGraph, key_line_map: Dict[str, Set[int]], file_path: str, ground_truth_file_path: str):
    """
    build XFGs
    Args:
        PDG (nx.DiGraph): program dependence graph
        key_line_map (Dict[str, Set[int]]): key lines
    Returns: XFG map
    """
    if PDG is None or key_line_map is None:
        return None
    # ct0, ct1 = 0, 0
    res = {"call": [], "array": [], "ptr": [], "arith": []}

    with open(ground_truth_file_path, "r") as ground_truth_file:
        ground_truth = json.load(ground_truth_file)
    for key in ["call", "array", "ptr", "arith"]:
        for ln in key_line_map[key]:
            sliced_lines = set()
            # print(ln)
            # backward traversal
            bqueue = list()
            visited = set()
            bqueue.append(ln)
            visited.add(ln)
            while bqueue:
                fro = bqueue.pop(0)
                sliced_lines.add(fro)
                if fro in PDG._pred:
                    for pred in PDG._pred[fro]:
                        if pred not in visited:
                            visited.add(pred)
                            bqueue.append(pred)
            # print(visited)
            # forward traversal
            fqueue = list()
            visited = set()
            fqueue.append(ln)
            visited.add(ln)
            while fqueue:
                fro = fqueue.pop(0)
                sliced_lines.add(fro)
                if fro in PDG._succ:
                    for succ in PDG._succ[fro]:
                        if succ not in visited:
                            visited.add(succ)
                            fqueue.append(succ)
            # print(visited)
            if len(sliced_lines) != 0:
                XFG = PDG.subgraph(list(sliced_lines)).copy()
                XFG.graph["key_line"] = ln
                if file_path in ground_truth:
                    VUL_FOUND_FILEANEM = "vul_found.txt"
                    if not isfile(VUL_FOUND_FILEANEM):
                        os.system(f"touch {VUL_FOUND_FILEANEM}")
                    with open(VUL_FOUND_FILEANEM, "a") as vff:
                        vff.write(f"{file_path}: {sliced_lines}\n")

                    if len(sliced_lines.intersection(ground_truth[file_path])) != 0:
                        XFG.graph["label"] = 1
                    else:
                        XFG.graph["label"] = 0
                else:
                    XFG.graph["label"] = 0
                res[key].append(XFG)
            # print()
    # print()
    return res


def dump_XFG(res: Dict[str, List[nx.DiGraph]], out_root_path: str,
             file_path: str):
    """
    dump XFG to file

    Args:
        res: XFGs
        out_root_path: output root path
        file_path: file_path
    Returns:
    """
    if res is None:
        return
     
    testcase_out_root_path = join(out_root_path, file_path)
    if not exists(testcase_out_root_path):
        os.makedirs(testcase_out_root_path)
    for k in res:
        k_root_path = join(testcase_out_root_path, k)
        if not exists(k_root_path):
            os.makedirs(k_root_path)
        for XFG in res[k]:
            out_path = join(k_root_path, f"{XFG.graph['key_line']}.xfg.pkl")
            nx.write_gpickle(XFG, out_path)


def configure_arg_parser() -> ArgumentParser:
    arg_parser = ArgumentParser()
    arg_parser.add_argument("-c",
                            "--config",
                            help="Path to YAML configuration file",
                            default="configs/dwk.yaml",
                            type=str)
    return arg_parser


@dataclasses.dataclass
class QueueMessage:
    XFG_res: Dict
    out_root_path: str
    file_path: str
    is_finished: bool = False


def handle_queue_message(queue: Queue):
    """

    Args:
        queue:

    Returns:

    """
    xfg_ct = 0
    while True:
        message: QueueMessage = queue.get()
        if message.is_finished:
            break
        if message.XFG_res is not None:
            dump_XFG(message.XFG_res, message.out_root_path, message.file_path)
            for k in message.XFG_res:
                xfg_ct += len(message.XFG_res[k])
    print(f'XFG_CNT: {xfg_ct}')
    return xfg_ct


def process_parallel(file_path: str, queue: Queue, done: Set, cwe_root: str,
                     source_root_path: str,
                     out_root_path: str):
    """

    Args:
        file_path:
        done:
        cwe_root:
        source_root_path:
        out_root_path:

    Returns:

    """
    if file_path in done:
        return file_path
    
    csv_path = join(cwe_root, "csv") + join(source_root_path, file_path)
    # print(csv_path)
    # csv_path = join(cwe_root, "csv", os.path.abspath(source_root_path)[1:], file_path)
    source_path = join(source_root_path, file_path)
    PDG, key_line_map = build_PDG(csv_path, "data/sensiAPI.txt",
                                    source_path)
    res = build_XFG(PDG, key_line_map, file_path, join(cwe_root, "ground_truth.json"))
    queue.put(QueueMessage(res, out_root_path, file_path))
    
    return file_path


def generate(config_path: str):
    config = cast(DictConfig, OmegaConf.load(config_path))
    root = config.data_folder
    cweid = config.dataset.name
    cwe_root = join(root, cweid)
    source_root_path = join(cwe_root, "source-code")
    out_root_path = join(cwe_root, "XFG")
    testcases = []

    file_set = []

    for dir_, _, files in os.walk(source_root_path):
        for file_name in files:
            rel_dir = os.path.relpath(dir_, source_root_path)
            rel_file = join(rel_dir, file_name)
            if rel_file.endswith(".c") or rel_file.endswith(".cpp") or rel_file.endswith(".h"):
                testcases.append(rel_file)

    

    os.system(f'rm -rf {out_root_path}')
    os.makedirs(out_root_path)

    os.system(f'rm {join(cwe_root, "done.txt")}')
    os.system("touch {}".format(join(cwe_root, "done.txt")))
    
    with open(join(cwe_root, "done.txt"), "r", encoding="utf-8") as f:
        done = set(f.read().split(","))

    testcase_len = len(testcases)
    with Manager() as m:
        message_queue = m.Queue()  # type: ignore
        pool = Pool(USE_CPU)
        xfg_ct = pool.apply_async(handle_queue_message, (message_queue,))
        process_func = functools.partial(process_parallel, queue=message_queue, done=done,
                                         cwe_root=cwe_root, source_root_path=source_root_path,
                                         out_root_path=out_root_path)
        testcases: List = [
            testcase
            for testcase in tqdm(
                pool.imap_unordered(process_func, testcases),
                desc=f"testcases: ",
                total=testcase_len,
            )
        ]

        message_queue.put(QueueMessage(None, None, None, True))
        pool.close()
        pool.join()
    print(f"total {xfg_ct.get()} XFGs!")    
    for file_path in testcases:
        with open(join(cwe_root, "done.txt"), 'a',
                  encoding="utf-8") as f:
            f.write(str(file_path))
            f.write(",")


if __name__ == "__main__":
    __arg_parser = configure_arg_parser()
    __args = __arg_parser.parse_args()
    generate(__args.config)
