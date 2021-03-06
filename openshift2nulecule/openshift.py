# -*- coding: utf-8 -*-

import logging
import anymarkup
import os

from openshift2nulecule import utils
from openshift2nulecule.constants import NULECULE_PROVIDERS

logger = logging.getLogger(__name__)


class OpenshiftClient(object):

    # path to oc binary
    oc = None

    namespace = None
    oc_config = None

    def __init__(self, oc=None, namespace=None, oc_config=None,
                 selector=None):
        if oc:
            self.oc = oc
        else:
            self.oc = self._find_oc()

        self.namespace = namespace
        self.selector = selector

        if oc_config:
            self.oc_config = utils.get_path(oc_config)

    def get_username(self):
        """
        Return the currently authenticated user name
        """
        ec, stdout, stderr = self._call_oc(["whoami"])
        if ec == 0:
            return stdout
        else:
            return None

    def get_token(self):
        """
        Get the token the current session is using.
        """
        ec, stdout, stderr = self._call_oc(["whoami", "-t"])
        if ec == 0:
            return stdout
        else:
            return None

    @staticmethod
    def _find_oc():
        """
        Determine the path to oc command
        Search /usr/bin:/usr/local/bin

        Returns:
            str: path to oc binary
        """

        test_paths = ['/usr/bin/oc', '/usr/local/bin/oc']

        for path in test_paths:
            test_path = utils.get_path(path)
            logger.debug("trying oc at " + test_path)
            oc = test_path
            if os.access(oc, os.X_OK):
                logger.debug("found oc at " + test_path)
                return oc
        logger.fatal("No oc found in {}. Please provide corrent path to co "
                     "binary using --oc argument".format(":".join(test_paths)))
        return None

    def _call_oc(self, args):
        """
        Runs a oc command with its arguments and returns the results.

        Args:
            args (list): arguments for oc command

        Returns:
            ec:     The exit code from the command
            stdout: stdout from the command
            stderr: stderr from the command
        """

        cmd = [self.oc]
        if self.oc_config:
            cmd.extend(["--config", self.oc_config])
        if self.namespace:
            cmd.extend(["--namespace", self.namespace])

        cmd.extend(args)

        ec, stdout, stderr = utils.run_cmd(cmd)

        return (ec, stdout, stderr)

    def export_project(self):
        """
        Export configuration from Openshift for various providers

        Returns:
            A dict with keys as provider and value as artifacts corresponding
            to that provider.
        """
        # Resources to export.
        # Don't export Pods for now.
        # Exporting ReplicationControllers should be enough.
        # Ideally this should detect Pods that are not created by
        # ReplicationController and only export those.
        # Order in resource list is significant! Object are exported in same
        # order as they are specified on command line and they will have same
        # order in Nulecule file also.
        # ImageStream is first as workaround for this https://github.com/openshift/origin/issues/4518
        # But this workaround is going to work only after resolving
        # https://github.com/projectatomic/atomicapp/issues/669
        all_artifacts = {}
        for provider in NULECULE_PROVIDERS:
            if provider == "kubernetes":
                resources = ["persistentVolumeClaim",
                             "service",
                             "replicationController"]
            elif provider == "openshift":
                resources = ["imageStream",
                             "service",
                             "persistentVolumeClaim",
                             "replicationController",
                             "deploymentConfig",
                             "buildConfig"]

            # output of this export is kind List
            args = ["export", ",".join(resources), "-o", "json"]
            # if user has specified the selector append it to command
            if self.selector:
                args.extend(["-l", self.selector])

            ec, stdout, stderr = self._call_oc(args)
            objects = anymarkup.parse(stdout, format="json", force_types=None)

            # convert OpenShift List to array
            if objects["kind"] == "List":
                artifacts = objects["items"]
            else:
                msg = "Output of `oc export` command is of diferent kind than 'List'"
                logger.critical(msg)
                raise Exception(msg)

            all_artifacts[provider] = artifacts

        ep = ExportedProject(artifacts=all_artifacts)

        return ep


class ExportedProject(object):
    artifacts = None

    # all images from all artifacts, gets updated with every image operation
    # like pull_images, push_images ...
    images = None

    def __init__(self, artifacts):

        self.artifacts = artifacts

        # get all images of all ReplicationControllers
        self.images = []
        for provider in NULECULE_PROVIDERS:
            for artifact in self.artifacts[provider]:
                # TODO: add support for other kinds (Pod, ....?)
                if artifact["kind"] in ["ReplicationController",
                                        "DeploymentConfig"]:
                    self.images.extend(utils.get_image_info(artifact))

        self._remove_imagestream_annotations()
        self._remove_openshift_objects()

    def _remove_openshift_objects(self):
        """
        Remove objects from OpenShift artifacts that were created automaticaly by
        other object.
        eg.: Remove ReplicationControllers that were created by DeploymentConfig
        """
        for obj in list(self.artifacts["openshift"]):
            if obj["kind"] == "ReplicationController":
                # check if this RC has been created by DC by checking if
                # openshift.io/deployment-config.name annotation exists
                deployment_config_name = obj.get("metadata", {}).get("annotations",{}).get("openshift.io/deployment-config.name",{})
                if deployment_config_name:
                    self.artifacts["openshift"].remove(obj)

    def pull_images(self, registry, username, password, only_internal=True):
        """
        This pulls all images that are mentioned in artifact.

        Args:
            registry (str): url of exposed OpenShift Docker registry
            username (str): username for for OpenShift Docker registry
            password (str): password for OpenShift Docker registry
            only_internal (bool): if True only images that are in internal
                                  OpenShift Docker registry, otherwise pulls
                                  all images (default is True)

        """
        logger.debug("Pulling images (only_internal: {}, registry:{},"
                     " login:{}:{})".format(only_internal, registry,
                                            username, password))

        ec, stdout, stderr = utils.run_cmd(['docker', 'login',
                                            '-u', username,
                                            '-p', password,
                                            '-e', "{}@{}".format(username,
                                                                 registry),
                                            registry])

        for image_info in self.images:
            if image_info["internal"]:
                image_info["image"] = utils.replace_registry_host(
                    image_info["image"], registry)
            else:
                if only_internal:
                    # we are exporting only internal images, skip this
                    continue
            image = image_info["image"]
            logger.info("Pulling image {}".format(image))

            ec, stdout, stderr = utils.run_cmd(['docker', 'pull', image])

    def push_images(self, registry, username, password, only_internal=True):
        """
        This pushes all images that are mentioned in artifact.

        Args:
            registry (str): url of registry
            username (str): username for docker registry. If None
                            (don't autheticate to registry)
            password (str): password for docker registry
            only_internal (bool): if True only images that are in internal
                                  OpenShift Docker registry, otherwise pulls
                                  all images (default is True)

        """
        logger.debug("pushing images to registry only_internal: {}, "
                     "registry:{}, login:{}:{}".format(only_internal, registry,
                                                       username, password))

        if username and password:
            ec, stdout, stderr = utils.run_cmd(['docker', 'login',
                                                '-u', username,
                                                '-p', password,
                                                '-e', "{}@{}".format(username,
                                                                     registry),
                                                registry])

        for image_info in self.images:
            if only_internal and not image_info["internal"]:
                # skip this image
                continue
            image = image_info["image"]

            # new name of image (only replace registry part)
            name_new_registry = utils.replace_registry_host(image, registry)

            (new_name, new_name_tag, new_name_digest) = utils.parse_image_name(
                name_new_registry)

            if new_name_digest:
                # if this is image with define digest, use digest as tag
                # docker cannot push image without tag, and if images
                # is pulled with digest it doesn't have tag specified

                # if this is going to be used as tag, it cannot contain ':'
                tag = new_name_digest.replace(":", "")
            else:
                tag = new_name_tag

            new_full_name = "{}:{}".format(new_name, tag)
            image_info["image"] = new_full_name

            logger.info("Tagging image {} as {}".format(image, new_full_name))

            ec, stdout, stderr = utils.run_cmd(['docker', 'tag', '-f', image,
                                                new_full_name])

            logger.info("Pushing image {}".format(new_full_name))
            ec, stdout, stderr = utils.run_cmd(['docker', 'push', new_full_name])

    def update_artifacts_images(self):
        """
        Update artifact images. When pulling and pushing images
        are renamed (retagged). This updates image names in
        all artifacts.
        """
        for provider in NULECULE_PROVIDERS:
            for artifact in self.artifacts[provider]:
                # TODO: add support for other kinds (Pod, ...?)
                if artifact["kind"] in ["ReplicationController", "DeploymentConfig"]:
                    for container in \
                            artifact["spec"]["template"]["spec"]["containers"]:
                        for image in self.images:
                            if container["image"] == image["original_image"]:
                                logger.info("Updating image {} for artifact {}:{}"
                                            .format(container["image"],
                                                    artifact["kind"],
                                                    artifact["metadata"]["name"]))
                                container["image"] = image["image"]

    def _remove_imagestream_annotations(self):
        """
        Remove annotations from all imageStreams.
        This is temporary workaround for https://github.com/openshift/origin/issues/8327
        """
        for obj in self.artifacts["openshift"]:
            if obj["kind"] == "ImageStream":
                if obj.get("metadata", {}).get("annotations", {}):
                    del obj["metadata"]["annotations"]
