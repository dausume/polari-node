# Keep your computer clean and running smooth
sudo docker system prune

# Dev - Building Polari in isolation
Look in the build-commands.md files of polari-framework and polari-framework for
the isolation-dev build commands.  You will only need to declare the polari-node
network once for the overall app.

Command for making the polari-node network (remove sudo if in non-linux environment):

    sudo docker network create polari-node-network

To create/build both of the downstream isolation builds at once:

    docker-compose -f polari-framework/docker-compose.yml build &
    docker-compose -f polari-platform-angular/docker-compose.yml build &
    wait

To spin up the isolation build stuff use this:

    docker-compose -f polari-framework/docker-compose.yml up -d &
    docker-compose -f polari-platform-angular/docker-compose.yml up -d &
    wait