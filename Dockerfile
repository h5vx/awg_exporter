FROM golang:1.25.5-alpine

COPY go.mod go.sum /go/src/

RUN apk add build-base && cd /go/src && go mod download
