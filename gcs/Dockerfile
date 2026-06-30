FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir flask
COPY gcs/ .
ENV PYTHONUNBUFFERED=1
CMD ["python", "app.py"]
