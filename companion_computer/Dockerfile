FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir pymavlink
COPY companion_computer/ .
ENV PYTHONUNBUFFERED=1
CMD ["python", "app.py"]
