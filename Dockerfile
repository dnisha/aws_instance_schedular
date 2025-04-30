FROM python:3.9
MAINTAINER Deepak Nishad "dnishad158@gmail.com"
COPY aws_scheduler /app
WORKDIR /app
RUN pip install --upgrade pip
RUN pip install -r requirements.txt
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
CMD flask run --host 0.0.0.0