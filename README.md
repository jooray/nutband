# nutband

Experimental minimalistic python-only user interface for cashu, using
[cashu nutshell](https://github.com/cashubtc/nutshell) implementation.

Should be buildable to apk and ipa through buildozer, running on android,
ios and desktop platforms. I have not managed to build anything else than
desktop now though.

## Video introduction

I made a short video introduction about this project:

[![Nutband - a post fiat apocalypse Cashu client working over Reticulum](https://i.ytimg.com/vi/HAX8GFn5uCI/mqdefault.jpg)](https://www.youtube.com/watch?v=HAX8GFn5uCI "Nutband - a post fiat apocalypse Cashu client working over Reticulum")

## Goal

The goal of this project is to experiment with cashu over [Reticulum](https://github.com/markqvist/Reticulum) mesh network protocol using
[LXMF](https://github.com/markqvist/LXMF).

## Status = short version

Only sending and receiving tokens is enabled, mint selection or invoices
do not work yet.

Very hacky!!!

## LXMF proxy on the mint side

This is how you run a proxy on the mint side. Proxy should be run by the mint
operator and users should verify the identity of the mint operator. Possibly
some mints could operate only through LXMF, but having correct address replaces
all certificate authentication, so be sure!

``` bash
python3 lxmf_proxy_server.py https://localhost:3338 localhost_mint
```

This runs the proxy that forwards all messages to localhost:3338. localhost_mint
is the name of the identity (in case you run more proxies).

## Mapping on the client side

The map is now static in source code:

``` python
            # TODO: Config this:
            mappings = {
                "https://8333.space:3338": "197b2a93cdcd63217f0c7c08950abcde"
            }
```

You should change the URL of the mint and the identity. URL does not have to work,
it can be bogus.

## Status and plans

Some things that I would like to improve:

- the radios have very low bandwidth. I refresh keysets on the launch, but that might not be the best idea, it can add a minute until launch. The UI is responsive though
- keyset sharing is very inefficient, I think an xpub based schema could work better. The mint could say "this xpub, derive keys according to standard denominations yourself". Not sure if it's interesting for mainstream cashu, maybe it could be a parameter during requesting keysets ("please give me your keysets, I'm OK with xpub, I can derive them myself").
- I should pack the jsons better, in binary form and compress it.

## Building

My build and dev environment [is dockerized](https://github.com/jooray/docker-xrdp).

### macOS

To build using buildozer on macOS you need to perform the following commands first:

``` bash
brew install libffi

export PKG_CONFIG_PATH="/opt/homebrew/opt/libffi/lib/pkgconfig"
export LDFLAGS="-L/opt/homebrew/opt/libffi/lib"
export CPPFLAGS="-I/opt/homebrew/opt/libffi/include"
```

## Useful pieces

The lxmf client and proxy are possibly useful beside this project. The client (`lxmf_wrapper_client.py`) has get and post methods that are somewhat compatible with httpx.AsyncClient API (somewhat = enough that nutband runs and nutshell library thinks it's talking to a http server).

The `lxmf_proxy_server.py` contains a standalone proxy that listens for LXMF requests, decodes them, sends them over through HTTP and delivers a reply over another LXMF message. Pairing is done using random IDs.
