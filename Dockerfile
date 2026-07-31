FROM python:3.11-slim
WORKDIR /app
COPY gemini-api.py requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8080
CMD ["python3", "gemini-api.py"]
