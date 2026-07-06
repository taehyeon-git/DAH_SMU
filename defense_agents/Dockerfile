FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY defense_agents ./defense_agents

CMD ["python", "-m", "defense_agents.orchestrator"]
