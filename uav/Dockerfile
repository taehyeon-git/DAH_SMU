FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install pymavlink

WORKDIR /app
COPY uav/mock_uav.py .

ENV PYTHONUNBUFFERED=1
CMD ["python3", "mock_uav.py"]
