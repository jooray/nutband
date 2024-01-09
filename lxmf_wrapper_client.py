import asyncio
import json
import RNS
import os
import time
import LXMF
import sys
import random
import string

class LXMFWrapperClient:

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LXMFWrapperClient, cls).__new__(cls)
        return cls._instance

    # Generate a 4-byte ASCII string
    def random_id(self):
        return ''.join(random.choices(string.ascii_letters + string.digits, k=4))


    def receive_handler(self, lxm):
        fields = lxm.fields
        req_id = fields.pop("req_id", None)
        if req_id == None:
            print("Received reply with req_id not set")
            return
        if not req_id in self.reply_callbacks:
            print(f"Received reply with unknown req_id {req_id}")
            return

        reply_callback, destination_hash = self.reply_callbacks[req_id]
        # Only call callbacks that come from the right source for the req_id
        # source_hash is signed, so it could not have come from anyone else
        if lxm.source_hash != destination_hash:
            print(f"Received reply for {req_id} from wrong source. Was expecting {lxm.source_hash}, got {destination_hash}")
            return

        del self.reply_callbacks[req_id]
        print (f"Calling reply_callback for {req_id}")
        reply_callback(req_id, lxm)


    async def send_lxmf_message(self, destination, content, fields,
                          delivery_callback, failed_callback,
                          reply_callback, req_id=None):
        # Convert string to bytes below if you pass as a string
        destination_bytes = bytes.fromhex(destination)

        # Check to see if RNS knows the identity
        destination_identity = RNS.Identity.recall(destination_bytes)

        # If it doesn't know the identity:
        if destination_identity == None:
            basetime = time.time()
            # Request it
            RNS.Transport.request_path(destination_bytes)
            # And wait until it arrives; timeout in 300s
            print("Don't have identity for " + destination + ", waiting for it to arrive for 300s")
            while destination_identity == None and (time.time() - basetime) < 300:
                destination_identity = RNS.Identity.recall()
                await asyncio.sleep(1)
        if destination_identity == None:
            print("Error: Cannot recall identity")
            sys.exit(1)

        lxmf_destination = RNS.Destination(
            destination_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "lxmf",
            "delivery"
            )

        if req_id is None:
            req_id = self.random_id()
        fields["req_id"]=req_id

        # Create the lxm object
        lxm = LXMF.LXMessage(
            lxmf_destination,
            self.local_lxmf_destination,
            content,
            fields=fields,
            desired_method=LXMF.LXMessage.DIRECT
            )

        if delivery_callback is not None:
            lxm.register_delivery_callback(delivery_callback)
        if failed_callback is not None:
            lxm.register_failed_callback(failed_callback)
        if reply_callback is not None:
            self.reply_callbacks[fields["req_id"]]=(reply_callback, lxmf_destination.hash)

        # Send the message through the router
        self.lxm_router.handle_outbound(lxm)


    def create_lxmf_proxy(self):

        # Initialize Reticulum. It's a singleton, we do it once per process
        if RNS.Reticulum.get_instance() is None:
            reticulum = RNS.Reticulum()

        # Reticulum / LXMF has permanent identity, but we specifically
        # don't want to be permanent, we will use per launch identity
        self.ID = RNS.Identity()

        userdir = os.path.expanduser("~")

        configdir = userdir+"/.lxmfproxy_client/"

        if not os.path.isdir(configdir):
            os.makedirs(configdir)


        self.lxm_router = LXMF.LXMRouter(identity = self.ID, storagepath = configdir)
        self.lxm_router.register_delivery_callback(lambda lxm: self.receive_handler(lxm))
        self.local_lxmf_destination = self.lxm_router.register_delivery_identity(self.ID,display_name="LXMFProxy")
        self.local_lxmf_destination.announce()

    def __init__(self):
        if (not hasattr(self, 'reply_callbacks')) or (self.reply_callbacks is None):
            self.reply_callbacks = {}
            self.create_lxmf_proxy()


class LXMFProxy:
    def __init__(self, lxmf_wrapper_client: LXMFWrapperClient, httpx=None, httpx_allowed=False, mappings=None):
        self.mappings = mappings
        if self.mappings is None:
            self.mappings = {}
        self.lxmf_wrapper_client = lxmf_wrapper_client
        self.httpx = httpx
        self.httpx_allowed = httpx_allowed
        self.futures = {}  # Dictionary to store futures mapped by req_id
        self.event_loop = asyncio.get_running_loop()

    def get_destination_for_url(self, url):
        destination = None
        new_url = url
        for map_url in self.mappings:
            if url.startswith(map_url):
                destination = self.mappings[map_url]
                new_url = url.replace(map_url, '')
                break

        return (destination, new_url)

    async def handle_request(self, method, url, *, data=None, json=None, headers=None, cookies=None, params=None, **kwargs):
        destination, new_url = self.get_destination_for_url(url)
        if destination is None:
            if self.httpx_allowed and self.httpx is not None:
                return await self.httpx.get(url, params=params, headers=headers, cookies=cookies, **kwargs)
            else:
                raise Exception(f"URL {url} not found in mappings and http(s) is disabled")
        else:
            fields = {}
            fields["method"] = method
            if data is not None:
                fields["data"] = data
            if json is not None:
                fields["json"] = json
            if params is not None:
                fields["params"] = params
            if headers is not None:
                fields["headers"] = headers
            if cookies is not None:
                fields["cookies"] = cookies

            def describe_request(lxm):
                req_id = ""
                method = ""
                if "req_id" in lxm.fields:
                    req_id = lxm.fields["req_id"]
                if "method" in lxm.fields:
                    method = lxm.fields["method"]
                return f"{method} request ID {req_id}"

            def delivery_callback(lxm):
                print("Delivered: " + describe_request(lxm))

            def failed_callback(lxm):
                request_description = describe_request(lxm)
                print("Failed: " + request_description)
                if "req_id" in lxm.fields:
                    req_id = lxm.fields["req_id"]
                    future = self.futures.get(req_id)
                    if future and not future.done():
                        del self.futures[req_id]
                        future.set_exception(Exception("Request failed: " + request_description))
                else:
                    raise Exception("Request failed: " + request_description)


            def failed_callback(lxm):
                request_description = describe_request(lxm)
                print("Failed: " + request_description)
                raise Exception("Request failed " + request_description)

            def reply_callback(req_id, lxm):
                response = lxm
                future = self.futures.pop(req_id, None)
                if future and not future.done():
                    try:
                        self.event_loop.call_soon_threadsafe(future.set_result, response)
                    except Exception as e:
                        print(e)

            req_id=self.lxmf_wrapper_client.random_id()
            future = asyncio.Future()
            self.futures[req_id] = future
            await self.lxmf_wrapper_client.send_lxmf_message(destination, new_url, fields,
                          delivery_callback, failed_callback,
                          reply_callback, req_id=req_id)
            lxm_reply = await future
            print (lxm_reply)
            return LXMFProxyResponse(lxm_reply)

    async def get(self, url, *, params=None, headers=None, cookies=None, **kwargs):
        return await self.handle_request('GET', url, params=params, headers=headers, cookies=cookies)

    async def post(self, url, *, data=None, json=None, headers=None, cookies=None, **kwargs):
        return await self.handle_request('POST', url, data=data, json=json, headers=headers, cookies=cookies)

class LXMFProxyResponse:

    def __init__(self, lxm):
        self.lxm = lxm
        self.content = lxm.content

    def json(self):
        return json.loads(self.text())

    def text(self):
        return self.content

    def raise_for_status(self):
        pass
