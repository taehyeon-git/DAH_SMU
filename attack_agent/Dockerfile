# 베이스 이미지: Python 3.11 (가벼운 버전)
FROM python:3.11-slim

# 컨테이너 안 작업 폴더 설정
WORKDIR /app

# requirements.txt 먼저 복사 후 패키지 설치
COPY requirements.txt .
RUN pip install -r requirements.txt

# 공격 에이전트 전체 복사
COPY attack_agent/mavlink_parser.py .
COPY attack_agent/recon.py .
COPY attack_agent/executor.py .
COPY attack_agent/jammer.py .
COPY attack_agent/spoofer.py .
COPY attack_agent/failsafe_inducer.py .

# 기본 실행은 recon.py (argparse로 파라미터 수신)
# executor/jammer/spoofer/inducer는 docker-compose command로 오버라이드
CMD ["python", "recon.py"]