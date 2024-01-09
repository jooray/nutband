# nutband

Experimental minimalistic python-only user interface for cashu, using
[cashu nutshell](https://github.com/cashubtc/nutshell) implementation.

Should be buildable to apk and ipa through buildozer, running on android,
ios and desktop platforms. I have not managed to build anything else than
desktop now though.

## Goal

The goal of this project is to experiment with cashu over [Reticulum](https://github.com/markqvist/Reticulum) mesh network protocol using
[LXMF](https://github.com/markqvist/LXMF).

We'll see where do I get.

## Status

Only sending and receiving tokens is enabled, mint selection or invoices 
do not work yet.

Very hacky!!!

## LXMF proxy on the mint side

This is how you run a proxy on the mint side. Proxy should be run by the mint
operator and users should verify the identity of the mint operator. Possibly
some mints could operate only through LXMF, but having correct address replaces
all certificate authentication, so be sure!

```
python3 lxmf_proxy_server.py https://localhost:3338 localhost_mint
```

This runs the proxy that forwards all messages to localhost:3338. localhost_mint
is the name of the identity (in case you run more proxies).

## Mapping on the client side

The map is now static in source code:

```python
            # TODO: Config this:
            mappings = {
                "https://8333.space:3338": "197b2a93cdcd63217f0c7c08950abcde"
            }
```

You should change the URL of the mint and the identity. URL does not have to work,
it can be bogus.

