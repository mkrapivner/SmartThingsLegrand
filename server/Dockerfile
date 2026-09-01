FROM python:3.13-alpine

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY legrand.py ./

# config.json is written at runtime by POST /init. It lives in its own directory
# so a volume can persist it without mounting over the code in /usr/src/app --
# a named volume is only seeded from the image while it is empty, so shadowing
# the code directory would pin legrand.py to its first-run version forever.
ENV LEGRAND_CONFIG=/data/config.json
VOLUME /data

EXPOSE 21120

# -u keeps logs unbuffered so `docker logs` shows them immediately
CMD ["python3", "-u", "legrand.py"]
