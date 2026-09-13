package docker

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
)

// One shared client: creating a client per call leaked a transport + socket each time.
var cli = &http.Client{Transport: &http.Transport{
	DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		var d net.Dialer
		return d.DialContext(ctx, "unix", socketPath())
	},
	MaxIdleConns: 1,
}}

// ponytail: only unix sockets, add tcp DOCKER_HOST if ever needed
func socketPath() string {
	if h := os.Getenv("DOCKER_HOST"); strings.HasPrefix(h, "unix://") {
		return strings.TrimPrefix(h, "unix://")
	}
	return "/var/run/docker.sock"
}

func call(ctx context.Context, method, path string, body any, out any) (io.ReadCloser, error) {
	var r io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		r = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, "http://docker"+path, r)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := cli.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 300 {
		defer resp.Body.Close()
		msg, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
		return nil, fmt.Errorf("docker %s %s: %s: %s", method, path, resp.Status, bytes.TrimSpace(msg))
	}
	if out == nil {
		return resp.Body, nil
	}
	defer resp.Body.Close()
	return nil, json.NewDecoder(resp.Body).Decode(out)
}

func ExecInContainerByName(
	ctx context.Context,
	containerName string,
	cmd []string,
) (stdout string, stderr string, exitCode int, err error) {
	var created struct{ Id string }
	_, err = call(ctx, "POST", "/containers/"+url.PathEscape(containerName)+"/exec",
		map[string]any{"Cmd": cmd, "AttachStdout": true, "AttachStderr": true}, &created)
	if err != nil {
		return "", "", -1, err
	}

	body, err := call(ctx, "POST", "/exec/"+created.Id+"/start",
		map[string]any{"Detach": false, "Tty": false}, nil)
	if err != nil {
		return "", "", -1, err
	}
	defer body.Close()

	// Docker multiplexes stdout/stderr: 8-byte header [stream,0,0,0,size uint32 BE] + payload
	var outBuf, errBuf bytes.Buffer
	var hdr [8]byte
	for {
		if _, err = io.ReadFull(body, hdr[:]); err != nil {
			if err == io.EOF {
				break
			}
			return "", "", -1, err
		}
		dst := &outBuf
		if hdr[0] == 2 {
			dst = &errBuf
		}
		if _, err = io.CopyN(dst, body, int64(binary.BigEndian.Uint32(hdr[4:]))); err != nil {
			return "", "", -1, err
		}
	}

	var inspect struct{ ExitCode int }
	if _, err = call(ctx, "GET", "/exec/"+created.Id+"/json", nil, &inspect); err != nil {
		return "", "", -1, err
	}
	return outBuf.String(), errBuf.String(), inspect.ExitCode, nil
}
