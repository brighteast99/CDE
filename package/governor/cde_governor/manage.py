import json
import os
from datetime import datetime
from typing import TypedDict

import docker
import natsort
from cde_governor.db import Database
from docker.models.containers import Container
from docker.types import DeviceRequest


class ManagerConfig(TypedDict):
    db: Database
    servers: list[tuple[str, int]]
    container_types: list[int]
    backup_dir: str
    cde_image: str
    cde_port: str


class Manager:
    def __init__(self, config: ManagerConfig):
        self.__db = config["db"]
        self.__servers = config["servers"]
        for server, _ in self.__servers:
            self.__get_docker_client(server)
            pass

        self.container_types = config["container_types"]

        self.__backup_dir = config["backup_dir"]
        if not os.path.exists(self.__backup_dir):
            os.mkdir(self.__backup_dir)

        self.__cde_image = config["cde_image"]
        self.__cde_port = config["cde_port"]

    def __get_docker_client(self, host: str, port: int = 2375) -> docker.DockerClient:
        if ":" in host:
            return docker.DockerClient(base_url=host)
        return docker.DockerClient(base_url=f"{host}:{port}")

    def __get_mapped_port(self, container: Container) -> str:
        container.reload()
        return container.attrs["NetworkSettings"]["Ports"][self.__cde_port][0][
            "HostPort"
        ]

    def __get_idle_resource(self) -> tuple[str, int]:
        allocations = self.__db.inspect_container_allocation()
        print(allocations)
        return min(
            [(host, gpu) for (host, gpus) in self.__servers for gpu in range(gpus)],
            key=lambda resource: (
                allocations[resource] if resource in allocations else 0
            ),
        )

    def create_cde(self, user_id: int, username: str) -> list[Container]:
        idle_host, idle_gpu = self.__get_idle_resource()
        client = self.__get_docker_client(idle_host)
        created_containers = [
            client.containers.run(
                self.__cde_image,
                name=f"{username}_{container_type}",
                stdin_open=True,
                tty=True,
                detach=True,
                labels={
                    "host": idle_host,
                    "gpu": str(idle_gpu),
                    "type": str(container_type),
                },
                ports={self.__cde_port: None},
                device_requests=[
                    DeviceRequest(
                        driver="nvidia",
                        device_ids=[str(idle_gpu)],
                        capabilities=[["gpu"]],
                    )
                ],
            )
            for container_type in self.container_types
        ]

        for container in created_containers:
            self.__db.save_container_info(
                id=container.id,
                host=idle_host,
                gpu=idle_gpu,
                user_id=user_id,
                container_type=container.labels.get("type"),
            )

        return created_containers

    def get_cde_url(self, user_id: int, container_type: int) -> str:
        assert container_type in self.container_types

        host, container_id = self.__db.get_container(user_id, container_type)
        client = docker.DockerClient(f"{host}:2375")
        container = client.containers.get(container_id)

        assert container is not None

        if container.status != "running":
            container.start()

        port = self.__get_mapped_port(container)
        return f"http://{host}:{port}"

    def get_container(
        self,
        host: str = "",
        container_id: str = "",
        user_id: int = 0,
        container_type=-1,
    ) -> Container:
        if not host or not container_id:
            host, container_id = self.__db.get_container(user_id, container_type)

        client = self.__get_docker_client(host)
        return client.containers.get(container_id)

    def backup_container(
        self,
        container: Container | None = None,
        user_id: int = 0,
        container_type: int = -1,
    ) -> None:
        if container is None:
            container = self.get_container(
                user_id=user_id, container_type=container_type
            )

        stream, _ = container.get_archive("/workspace")
        dir_path = f"{self.__backup_dir}/{container.name}"
        if not os.path.exists(dir_path):
            os.mkdir(dir_path)
        backup_path = dir_path + f'/{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}.tar'

        with open(backup_path, "wb") as f:
            for chunk in stream:
                f.write(chunk)

        return backup_path

    def backup_containers(self) -> None:
        containers = self.__db.get_containers()
        for host, container_id in containers:
            container = self.get_container(host, container_id)
            self.backup_container(container=container)

    def upload_file(
        self,
        container: Container | None = None,
        file=None,
        upload_to: str = "/workspace",
        user_id: int = 0,
        container_type: int = -1,
    ) -> None:
        if container is None:
            container = self.get_container(
                user_id=user_id, container_type=container_type
            )

        container.exec_run(f"mkdir -p {upload_to}")
        container.put_archive(upload_to, file)
