FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py remote.py discovery.py ./
COPY static ./static
RUN mkdir -p /data

ENV ONNREMOTE_DATA_DIR=/data
EXPOSE 4897
VOLUME ["/data"]
CMD ["python", "app.py"]
