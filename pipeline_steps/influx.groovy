def setup(){
  instance_name = "INFLUX"
  pubCloudSlave.getPubCloudSlave(instance_name: instance_name)
  common.override_inventory()
  try{
    common.conditionalStage(
      stage_name: "Influx",
      stage:{
        withCredentials([
          file(
            credentialsId: 'id_rsa_cloud10_jenkins_file',
            variable: 'jenkins_ssh_privkey'
          ),
        ]){
          dir('rpc-maas/playbooks'){
            git branch: env.RPC_MAAS_BRANCH, url: env.RPC_MAAS_REPO
            // Create inventory
            // TODO
            sh """#!/bin/bash
            echo "[log_hosts] > ~/inventory"
            echo "$INSTANCE_NAME $INSTANCE_IP" >> ~/inventory"
            """
            // Run playbooks
            common.venvPlaybook(
              playbooks: [
                "maas-tigkstack-influxdb.yml",
              ],
              args: [
                "-i ~/inventory",
                "--private-key=\"${env.JENKINS_SSH_PRIVKEY}\""
              ],
              vars: [
                WORKSPACE: "${env.WORKSPACE}"
              ]
            ) //venvPlaybook
          } //dir
        } //withCredentials
      }) //conditionalStage
  } catch (e){
    print(e)
    throw e
  }finally{
    pubCloudSlave.delPubCloudSlave()
  }
} //func
return this
