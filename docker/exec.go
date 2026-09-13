package docker

import (
	"github.com/docker/docker/api/types"
	"github.com/docker/docker/api/types/filters"
	"github.com/docker/docker/client"
	"github.com/docker/docker/pkg/stdcopy"

	"bytes"
	"context"
	"fmt"
)

func ExecInContainerByName(
	ctx context.Context,
	containerName string,
	cmd []string,
) (stdout string, stderr string, exitCode int, err error) {

	cli, err := client.NewClientWithOpts(
		client.FromEnv,
		client.WithAPIVersionNegotiation(),
	)
	if err != nil {
		return "", "", -1, err
	}

	// Найти контейнер по имени
	args := filters.NewArgs()
	args.Add("name", containerName)

	containers, err := cli.ContainerList(ctx, types.ContainerListOptions{
		All:     false,
		Filters: args,
	})
	if err != nil {
		return "", "", -1, err
	}

	if len(containers) == 0 {
		return "", "", -1, fmt.Errorf("container %q not found or not running", containerName)
	}

	containerID := containers[0].ID

	// Создать exec
	execResp, err := cli.ContainerExecCreate(ctx, containerID, types.ExecConfig{
		Cmd:          cmd,
		AttachStdout: true,
		AttachStderr: true,
	})
	if err != nil {
		return "", "", -1, err
	}

	// Подключиться
	attachResp, err := cli.ContainerExecAttach(ctx, execResp.ID, types.ExecStartCheck{})
	if err != nil {
		return "", "", -1, err
	}
	defer attachResp.Close()

	var outBuf, errBuf bytes.Buffer

	// Docker multiplexes stdout/stderr
	_, err = stdcopy.StdCopy(&outBuf, &errBuf, attachResp.Reader)
	if err != nil {
		return "", "", -1, err
	}

	// Получить exit code
	inspect, err := cli.ContainerExecInspect(ctx, execResp.ID)
	if err != nil {
		return "", "", -1, err
	}

	return outBuf.String(), errBuf.String(), inspect.ExitCode, nil
}
