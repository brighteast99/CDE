open=$PWD
apt-get update && apt-get install -y mariadb=1.1.3
pip install -r requirements.txt

cd package/governor && \
pip install -e . && \
cd ../logger && \
pip install -e . && \
cd ../../
python3 launch.py
