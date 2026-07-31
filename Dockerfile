FROM python:alpine
COPY . /app
WORKDIR /app
CMD ["python", "gemini-api.py"]
