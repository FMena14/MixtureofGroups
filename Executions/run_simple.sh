#!/bin/bash
cd simple_tol3
python ../../run_simple.py -M 3 -p ../../ -s 1 > result_scenario1.txt
python ../../run_simple.py -M 3 -p ../../ -s 2 > result_scenario2.txt
python ../../run_simple.py -M 3 -p ../../ -s 3 > result_scenario3.txt
python ../../run_simple.py -M 3 -p ../../ -s 4 > result_scenario4.txt
python ../../run_simple.py -M 3 -p ../../ -s 5 > result_scenario5.txt
python ../../run_simple.py -M 3 -p ../../ -s 6 > result_scenario6.txt
python ../../run_simple.py -M 3 -p ../../ -s 7 > result_scenario7.txt
cd ..
