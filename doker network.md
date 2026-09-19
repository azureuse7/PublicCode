Below is a step-by-step explanation of Docker’s basic networking concepts, focusing on how containers can talk to each other and how you, as a developer or DevOps engineer, can make that happen seamlessly.

-----
## **1. The Problem: Two Containers Need to Communicate**
Let’s say you have two separate containers:

1. **notes-api** (running Express.js)
1. **notes-api-db-server** (running PostgreSQL)

Initially, these two containers do not know how to find each other. You need some way to let one container (the Express.js API) talk to the other (PostgreSQL).
### (a) Trying to Use localhost (127.0.0.1)
If you try to connect from **notes-api** to 127.0.0.1:5432, it won’t work. Inside a container, 127.0.0.1 refers to that container’s own loopback interface. So, your Express.js container is only talking to itself—not the PostgreSQL container.
### (b) Trying to Use the Other Container’s IP Directly
You could use docker container inspect on the PostgreSQL container to find its IP address (e.g., 172.17.0.2). Then you could tell your Express.js container to connect to 172.17.0.2:5432. This might work temporarily but is **not recommended**:

- IP addresses can change whenever you recreate the container.
- Hardcoding IPs in configuration is prone to errors.

Hence, these approaches are quick hacks but not robust solutions.

-----
## **2. The Recommended Approach: User-Defined Bridge Networks**
Docker networking works by having **network objects** that containers can join. By default, Docker provides these networks:

- **bridge** (default Docker network)
- **host**
- **none**
- **overlay** (for multi-host or Swarm mode)
- **macvlan**

When you just run containers without specifying a network, Docker puts them on the default **bridge** network. While this default bridge network lets you communicate via IP addresses, you still face the issues mentioned before (manual IP usage, potential conflicts, etc.).

**User-defined bridge networks** solve these problems:

1. **Automatic DNS Resolution**: Containers on the same user-defined network can refer to each other by **name**. For example, the container named notes-db can be resolved by other containers simply as notes-db, without needing an IP address.
1. **Better Isolation**: Only containers that explicitly join the user-defined network can communicate on it, which reduces the chance of unwanted interactions.
1. **Easy Connect/Disconnect**: You can attach or detach containers on the fly to a user-defined network, without having to stop and recreate them.
-----
## **3. Creating and Managing a User-Defined Bridge Network**
### (a) Create a New Network
bash

```bash

docker network create skynet
```
- This creates a **bridge**-type network called skynet.
- You can confirm by running docker network ls, which will list your custom network along with the default ones.
### (b) Run a Container on That Network
If you want your container to be connected only to skynet, you can specify the network during container creation/run:

bash

```bash

docker container run \
  --network skynet \
  --name notes-api \
  -d <image-name>

```
Now notes-api is on the skynet network.
### (c) Connect an Existing Container to the Network
If you already have a running container (for example, hello-dock), you can connect it to your custom network with:

bash

```bash

docker network connect skynet hello-dock
```
Run docker network inspect skynet to see which containers are attached to it.
### (d) Container Name Resolution
If you have two containers on the same user-defined bridge network:

- Container A: notes-api
- Container B: notes-db

Container A can simply talk to notes-db by using the host notes-db (DNS name). For example:

js

```bash

// In your Express.js code:
const db = require('pg').Client({ 
  host: 'notes-db',
  port: 5432, 
  // ...other options...
});

```
No more IP juggling!

-----
## **4. Detaching and Removing Networks**
### (a) Detach a Container from a Network
bash

```bash

docker network disconnect skynet hello-dock
```
Docker won't give any output, but that container is now detached from skynet.
### (b) Remove a Network
bash

```bash

docker network rm skynet
```
If you have multiple networks and want to clean up all **unused** ones:

bash

```bash

docker network prune
```
-----
## **5. Summary**
1. **Localhost (127.0.0.1) only refers to the same container**
   So you can’t talk to another container’s process by using 127.0.0.1.
1. **Using IP addresses directly is fragile**
   Docker container IPs can change, and it’s cumbersome to manage them in code or config files.
1. **Use user-defined networks**
   1. **Automatic DNS**: Container names resolve to each other automatically.
   1. **Isolation**: Only containers on the same network can talk to each other.
   1. **Flexibility**: Attach/detach on the fly.

By leveraging Docker’s user-defined bridge networks, you can ensure your containers communicate in a clean, reliable, and maintainable way, without getting bogged down by ephemeral IP addresses or exposing services unnecessarily on the host system.

