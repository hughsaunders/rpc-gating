def deploy(){
  common.conditionalStep(
    step_name: "Deploy",
    step: {
      playbooks = common._parse_json_string(json_text: env.PLAYBOOKS)
      print(playbooks)
      print(playbooks.size())
      for (def i=0; i<playbooks.size(); i++){
        playbook = playbooks[i]
        print(playbook)
        stage(playbook.playbook){
          common.openstack_ansible(playbook)
        } //stage
      } // for
    } //  step
  ) //conditionalStep
}

def deploy_sh(Map args) {
  common.conditionalStage(
    stage_name: "Deploy RPC w/ Script",
    stage: {
      environment_vars = args.environment_vars + common.get_deploy_script_env()
      withEnv(environment_vars) {
        ansiColor('xterm') {
          if (!('vm' in args)){
            dir("/opt/rpc-openstack/") {
              sh """#!/bin/bash
              scripts/deploy.sh
              """
            } // dir
          } else {
            export_vars = ""
            for ( e in environment_vars ) {
              export_vars += "export ${e}; "
            }
            sh """#!/bin/bash
            sudo ssh -T -oStrictHostKeyChecking=no ${args.vm} \
              '${export_vars} cd /opt/rpc-openstack; scripts/deploy.sh'
            """
          } // if vm
        } // ansiColor
      } // withEnv
    } // stage
  ) // conditionalStage
}

void create_test_resource_load(Integer servers, Integer networks, Integer volumes) {
  dir("/opt/rpc-openstack/scripts/leapfrog/playbooks/") {
    sh """
      openstack-ansible \
        -i /opt/rpc-openstack/openstack-ansible/playbooks/inventory/dynamic_inventory.py \
        -e server_count=$servers \
        -e network_count=$networks \
        -e volume_count=$volumes \
        generate-resources.yml
    """
  }
}

def upgrade(String stage_name, String upgrade_script, List env_vars) {
  common.conditionalStage(
    stage_name: stage_name,
    stage: {
      environment_vars = env_vars + common.get_deploy_script_env()
      withEnv(environment_vars){
        dir("/opt/rpc-openstack/openstack-ansible"){
          sh "git reset --hard"
        }
        common.prepareRpcGit()
        if ( stage_name == "Leapfrog Upgrade" ) {
          create_test_resource_load(
            env.LEAPFROG_TEST_SERVERS as Integer,
            env.LEAPFROG_TEST_NETWORKS as Integer,
            env.LEAPFROG_TEST_VOLUMES as Integer,
          )
        }
        dir("/opt/rpc-openstack"){
          sh """
            scripts/$upgrade_script
          """
        } // dir
      } // withEnv
    } // stage
  ) // conditionalStage
}

def upgrade_minor(Map args) {
  upgrade("Minor Upgrade", "deploy.sh", args.environment_vars)
}

def upgrade_major(Map args) {
  upgrade("Major Upgrade", "test-upgrade.sh", args.environment_vars)
}

def upgrade_leapfrog(Map args) {
  upgrade("Leapfrog Upgrade", "leapfrog/ubuntu14-leapfrog.sh", args.environment_vars)
}

return this;
