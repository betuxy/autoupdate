python3 ./testing/i2.py install
alias i2="i2 --user autoupdate --password Pain-Frequently-Mother-Sun3-Instead -k"
alias run="ap testing/simulate-critical.yml && ap -i testing/inventory.yml autoupdate.yml"
# run1 agent1  →  simulate CRITICALs on agent1 only, then update only agent1
run1() { ap testing/simulate-critical.yml -e "sim_hosts=$1" && ap -i testing/inventory.yml autoupdate.yml -l "$1"; }
