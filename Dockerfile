FROM python:3.11-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

# 根据 CMD 传参决定运行哪个脚本，默认 main.py
CMD ["python", "main.py"]
