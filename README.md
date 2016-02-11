# OpenShift2Nulecule

This tool is creating new Nulecule application with Kubernetes
artifacts from OpenShift project.


Right now it only works for projects that are using Docker Images from public
registeries.
It is not working for projects that were builded in OpenShift.


# External dependencies
OpenShift client (`oc`) has to be installed and configured to 
connect to OpenShift server.

# Example
```
openshift2nulecule --output=/path/to/new/myapp --project=myproject
```
This will export whole project `myproject` from OpenShift 
and create new Nulecule application in `/path/to/new/myapp` directory.

# Notes

## How To
 - Exposing the Registry - https://docs.openshift.org/latest/install_config/install/docker_registry.html#exposing-the-registry

