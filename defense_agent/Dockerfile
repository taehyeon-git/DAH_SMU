# 베이스 이미지: Python 3.11 (가벼운 버전)
FROM python:3.11-slim

# 컨테이너 안 작업 폴더 설정
WORKDIR /app

# requirements.txt 먼저 복사 후 패키지 설치
COPY requirements.txt .
RUN pip install -r requirements.txt

# defense agent compatibility files and new four-agent package
COPY defense_agent ./defense_agent
COPY defense_agents ./defense_agents

# 방어 4-Agent orchestrator 실행
CMD ["python", "-m", "defense_agents.orchestrator"]
