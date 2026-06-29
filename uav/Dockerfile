FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 python3-pip wget \
    libexpat1 libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install pymavlink

# ArduPilot SITL 바이너리 (x86_64 Linux 프리빌드)
RUN wget -q \
    https://firmware.ardupilot.org/Plane/stable/SITL_x86_64_linux_gnu/arduplane \
    -O /usr/local/bin/arduplane \
    && chmod +x /usr/local/bin/arduplane

WORKDIR /app
COPY uav/sitl_runner.py .

ENV PYTHONUNBUFFERED=1
CMD ["python3", "sitl_runner.py"]
