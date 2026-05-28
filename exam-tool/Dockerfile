# 使用官方 Python 3.12 基础镜像
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 复制当前目录下的 requirements.txt 到容器中并安装依赖
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 将 exam-tool 目录下的所有文件复制到容器的工作目录 /app
COPY . /app

# 声明容器内部监听端口（可通过环境变量 PORT 在运行时动态修改）
EXPOSE 8002

# 启动应用程序
CMD ["python", "app.py"]
