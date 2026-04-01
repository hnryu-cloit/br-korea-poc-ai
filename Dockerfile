FROM python:3.10-slim

WORKDIR /app

# 시스템 의존성 설치 (numpy, faiss 등을 위해 필요할 수 있음)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# AI 서비스는 6001 포트 사용 (내부 8001로 띄우고 매핑 가능하지만 편의상 맞춤)
EXPOSE 6001

# run.py 내부에서 uvicorn을 실행하므로 해당 파일 실행
CMD ["python", "run.py"]
