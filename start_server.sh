#!/bin/bash

# Log file name prefix
BASE_LOG_FILE="graph_server_"

for PORT in {20050..20050}; do

  # ps -ef | grep $PORT | grep -v grep | awk '{print $2}' | xargs kill -9
  # sleep 2

  PID=$(lsof -i:$PORT -t)
  # Kill any process already bound to this port
  if [ ! -z "$PID" ]; then
    echo "Port $PORT is in use by PID $PID; attempting to kill..."
    kill -9 $PID
    echo "Process terminated."
  fi
  sleep 2

  # Start FastAPI app in the background (output redirected via nohup)
  echo "Starting FastAPI app on port $PORT..."
  echo $BASE_LOG_FILE$PORT.log
    # GRAPH_SERVER_LOG_FILE=$BASE_LOG_FILE$PORT nohup uvicorn graph_server:app --workers 5 --host 0.0.0.0 --port $PORT --timeout-keep-alive 3 &
    GRAPH_SERVER_LOG_FILE=$BASE_LOG_FILE$PORT nohup gunicorn -w 10 -k uvicorn.workers.UvicornWorker -t 600 graph_server:app --bind 0.0.0.0:$PORT --preload > log.out 2>&1 &
  echo "FastAPI app started; log file: ./logs/$BASE_LOG_FILE$PORT.log"
done