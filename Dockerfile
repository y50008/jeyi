FROM python:3.11-slim
WORKDIR /app
COPY . .
# 零依赖，无需 pip install —— 这正是本项目的设计声明
CMD ["python", "demo.py"]
