#!/bin/bash

source /etc/environment
ulimit -n 40000
neo4j-2.1.5/bin/neo4j console
neo4j-2.1.5/bin/neo4j restart
neo4j-2.1.5/bin/neo4j stop
neo4j-2.1.5/bin/neo4j start-no-wait
PYTHONPATH="Implementation/source2slice" python Implementation/source2slice/get_cfg_relation.py
neo4j-2.1.5/bin/neo4j stop