FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY wiktac_agent /app/wiktac_agent
EXPOSE 3180
CMD ["uvicorn","wiktac_agent.wiktac_agent:app","--host","0.0.0.0","--port","3180","--log-level","info"]
