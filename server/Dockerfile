
FROM python:3.13-alpine
 
WORKDIR /usr/src/app
 
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
 
COPY legrand.py ./
 
# config.json is written here at runtime by POST /init.
# Mount a volume on /usr/src/app if you want it to survive container rebuilds.
ENV LEGRAND_CONFIG=/usr/src/app/config.json
 
EXPOSE 21120
 
# -u keeps logs unbuffered so `docker logs` shows them immediately
CMD ["python3", "-u", "legrand.py"]
