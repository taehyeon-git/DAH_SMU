FROM python:3.11-slim

WORKDIR /app

COPY tactical_router/router.py .

ENV PYTHONUNBUFFERED=1
CMD ["python", "router.py"]
