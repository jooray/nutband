import asyncio
import RNS
import os
import time
import LXMF
import sys
import httpx

class LXMFWrapperProxy:

    async def receive_handler_async(self, lxm):
        fields = lxm.fields
        req_id = fields.pop("req_id", None)
        if req_id == None:
            print("Warning: Received request without req_id, ignoring")
            return None

        if "method" not in lxm.fields:
            print(f"Warning: Received request without method, ignoring")
            return None

        method = lxm.fields["method"]
        if method != "GET" and method != "POST":
            print(f"Warning: Received request with unsupported method {method}, ignoring")
            return None

        print(f"Got a request with ID {req_id} for method {method}")

        destination_bytes = lxm.source_hash
        destination_identity = RNS.Identity.recall(destination_bytes)
        # If we don't know the identity yet:
        if destination_identity == None:
            basetime = time.time()
            # Request it
            RNS.Transport.request_path(destination_bytes)
            # And wait until it arrives; timeout in 30s
            while destination_identity == None and (time.time() - basetime) < 30:
                destination_identity = RNS.Identity.recall(destination_bytes)
                await asyncio.sleep(1)
        if destination_identity == None:
            print("Error: Cannot recall identity")
            return None

        # Create the destination
        lxmf_destination = RNS.Destination(
        destination_identity,
        RNS.Destination.OUT,
        RNS.Destination.SINGLE,
        "lxmf",
        "delivery"
        )


        # Let's create the HTTP request
        url = self.destination_url + lxm.content_as_string()
        params = None
        headers = None
        cookies = None
        data = None
        json = None

        print(f"Crafting http request to {url}")


        if "params" in lxm.fields:
            params = lxm.fields["params"]
        if "headers" in lxm.fields:
            headers = lxm.fields["headers"]
        if "cookies" in lxm.fields:
            cookies = lxm.fields["cookies"]
        if "data" in lxm.fields:
            data = lxm.fields["data"]
        if "json" in lxm.fields:
            json = lxm.fields["json"]

        resp = None
        try:
            if (method == "GET"):
                print(f"Doing GET request to {url}")
                resp = await self.httpx.get(url, params=params, headers=headers, cookies=cookies)
            elif (method == "POST"):
                print(f"Doing POST request to {url}")
                resp = await self.httpx.post(url, params=params, data=data, json=json, headers=headers, cookies=cookies)
            resp.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            print(f"An error occurred while handling the HTTP request: {exc}")
            return None
        if resp is None:
            print("No response was received.")
            return None

        fields = {}
        fields["req_id"] = req_id
        # Create the lxm object
        lxm_outbound = LXMF.LXMessage(
        lxmf_destination,
        self.local_lxmf_destination,
        resp.text,
        title="ACK",
        fields=fields,
        desired_method=LXMF.LXMessage.DIRECT
        )

        def outbound_delivery_callback(message):
            print("Message delivered")

        # TODO: Register callbacks and retry delivery on failed
        lxm_outbound.register_delivery_callback(outbound_delivery_callback)
        # Send the message through the router
        print("Sending message")
        await self.lxm_router.handle_outbound(lxm_outbound)
        print("Message sent")

    def receive_handler(self, lxm):
        global loop
        try:
            asyncio.run_coroutine_threadsafe(self.receive_handler_async(lxm), loop)
        except Exception as e:
            print("Exception in receive handler: "+str(e))


    def send_announce(self):
        self.local_lxmf_destination.announce()

    def __init__(self, destination_url, identity_config, identity_name="LXMFProxyServer"):
        self.destination_url = destination_url

        # Name in bytes for transmission purposes
        namebytes = bytes(identity_name,"utf-8")

        # Initialize Reticulum
        reticulum = RNS.Reticulum()

        userdir = os.path.expanduser("~")

        mainconfigdir = userdir+"/.lxmfproxy/"

        if not os.path.isdir(mainconfigdir):
            os.makedirs(mainconfigdir)

        configdir = mainconfigdir+"/"+identity_config

        if not os.path.isdir(configdir):
            os.makedirs(configdir)

        identitypath = configdir+"/identity"
        if os.path.exists(identitypath):
           self.ID = RNS.Identity.from_file(identitypath)
        else:
            self.ID = RNS.Identity()
            self.ID.to_file(identitypath)
            print(f"Created new identity and saved key to {identitypath}...")

        self.lxm_router = LXMF.LXMRouter(identity = self.ID, storagepath = configdir)
        self.local_lxmf_destination = self.lxm_router.register_delivery_identity(self.ID,display_name=identity_name)
        self.local_lxmf_destination.announce()
        print(f"Running proxy with identity {RNS.prettyhexrep(self.local_lxmf_destination.hash)} redirecting to {self.destination_url}")

        self.lxm_router.register_delivery_callback(lambda lxm: self.receive_handler(lxm))

        # initialize self.httpx
        proxies_dict = {}
        #proxy_url: Union[str, None] = None
        #if settings.tor and TorProxy().check_platform():
        #    self.tor = TorProxy(timeout=True)
        #    self.tor.run_daemon(verbose=True)
        #    proxy_url = "socks5://localhost:9050"
        #elif settings.socks_proxy:
        #    proxy_url = f"socks5://{settings.socks_proxy}"
        #elif settings.http_proxy:
        #    proxy_url = settings.http_proxy
        #if proxy_url:
        #    proxies_dict.update({"all://": proxy_url})

        headers_dict = {"Client-version": "lxmf-proxy"}

        # Verify TLS certificates - if we connect to localhost, this can
        # be false, but then we can also connect to http, so defaults to true
        verify = True

        self.httpx = httpx.AsyncClient(
            verify=verify,
            proxies=proxies_dict,  # type: ignore
            headers=headers_dict,
            base_url=self.destination_url,
            timeout=5,
        )

async def main_event_loop(destination_url, identity_name, announce_delay_time):
    print("Initializing proxy...")
    proxy = LXMFWrapperProxy(destination_url, identity_name)
    print("Listening for requests...")

    oldtime = 0
    while True:
        newtime = time.time()
        if newtime > (oldtime + announce_delay_time):
            oldtime = newtime
            proxy.send_announce()
            print("Sent announce to the network...")
        await asyncio.sleep(1)

loop = None
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.set_debug(True)

    if (len(sys.argv) < 3):
        print("Usage: python3 lxmf_proxy_server.py <destination_url> <identity_name> [<announce_delay_time>]")
        sys.exit(1)

    announce_delay_time = 60*30
    if len(sys.argv) > 3:
       announce_delay_time = int(sys.argv[3])

    loop.run_until_complete(main_event_loop(sys.argv[1], sys.argv[2], announce_delay_time))
    loop.close()
