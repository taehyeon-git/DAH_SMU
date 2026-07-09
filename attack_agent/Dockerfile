# 베이스 이미지: Python 3.11 (가벼운 버전)
FROM python:3.11-slim

# 컨테이너 안 작업 폴더 설정
WORKDIR /app

# requirements.txt 먼저 복사 후 패키지 설치
COPY requirements.txt .
RUN pip install -r requirements.txt

# 공격 에이전트 전체 복사
COPY attack_agent ./attack_agent

# 기본 실행은 패키지 모듈 엔트리포인트
CMD ["python", "-m", "attack_agent.recon"]
