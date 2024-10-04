#!/usr/bin/env python

import asyncio
import os
import time
from datetime import datetime
from functools import wraps
from itertools import groupby, islice
from operator import itemgetter
from os import listdir
from os.path import isdir, join
import traceback
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup

from lxmf_wallet.wallet import Wallet as Wallet

from loguru import logger

from helpers import verify_mint

from cashu.core.base import TokenV3
from cashu.core.helpers import sum_proofs
from cashu.core.settings import settings
from cashu.nostr.client.client import NostrClient
from cashu.wallet.crud import (
    get_lightning_invoices,
    get_reserved_proofs,
    get_seed_and_mnemonic,
)
from cashu.wallet.helpers import (
    deserialize_token_from_string,
    init_wallet,
    list_mints,
)

# from cashu.nostr import receive_nostr, send_nostr


walletname = "wallet"
wallet = None


# https://github.com/pallets/click/issues/85#issuecomment-503464628
def coro(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return wrapper


async def redeem_TokenV3_multimint(wallet: Wallet, token: TokenV3):
    """
    Helper function to iterate thruogh a token with multiple mints and redeem them from
    these mints one keyset at a time.
    """
    for t in token.token:
        assert t.mint, Exception(
            "redeem_TokenV3_multimint: multimint redeem without URL"
        )
        mint_wallet = await Wallet.with_db(
            t.mint, os.path.join(settings.cashu_dir, wallet.name)
        )

        keysets = mint_wallet._get_proofs_keysets(t.proofs)
        logger.debug(f"Keysets in tokens: {keysets}")
        # loop over all keysets
        for keyset in set(keysets):
            await mint_wallet.load_mint()
            # redeem proofs of this keyset
            redeem_proofs = [p for p in t.proofs if p.id == keyset]
            _, _ = await mint_wallet.redeem(redeem_proofs)
            print(f"Received {sum_proofs(redeem_proofs)} sats")


async def receive(
    wallet: Wallet,
    tokenObj: TokenV3,
):
    logger.debug(f"receive: {tokenObj}")

    includes_mint_info: bool = any([t.mint for t in tokenObj.token])

    if includes_mint_info:
        # redeem tokens with new wallet instances
        await redeem_TokenV3_multimint(
            wallet,
            tokenObj,
        )
    else:
        raise Exception("no mint info retrieved.")

    # reload main wallet so the balance updates
    await wallet.load_proofs(reload=True)
    wallet.status()
    return wallet.available_balance


async def pay(ctx, invoice: str, yes: bool):
    wallet: Wallet = ctx.obj["WALLET"]
    await wallet.load_mint()
    wallet.status()
    total_amount, fee_reserve_sat = await wallet.get_pay_amount_with_fees(invoice)
    if not yes:
        potential = (
            f" ({total_amount} sat with potential fees)" if fee_reserve_sat else ""
        )
        message = f"Pay {total_amount - fee_reserve_sat} sat{potential}?"
        logger.debug(f"pay: {message}")
        # click.confirm(
        #    message,
        #    abort=True,
        #    default=True,
        # )

    print("Paying Lightning invoice ...", end="", flush=True)
    assert total_amount > 0, "amount is not positive"
    if wallet.available_balance < total_amount:
        print("Error: Balance too low.")
        return
    _, send_proofs = await wallet.split_to_send(wallet.proofs, total_amount)
    try:
        melt_response = await wallet.pay_lightning(
            send_proofs, invoice, fee_reserve_sat
        )

    except Exception as e:
        print(f"\nError paying invoice: {str(e)}")
        return
    print(" Invoice paid", end="", flush=True)
    if melt_response.preimage and melt_response.preimage != "0" * 64:
        print(f" (Proof: {melt_response.preimage}).")
    else:
        print(".")
    wallet.status()


async def invoice(amount: int, id: str, split: int, no_check: bool):
    wallet: Wallet = ctx.obj["WALLET"]
    await wallet.load_mint()
    wallet.status()
    # in case the user wants a specific split, we create a list of amounts
    optional_split = None
    if split:
        assert amount % split == 0, "split must be divisor or amount"
        assert amount >= split, "split must smaller or equal amount"
        n_splits = amount // split
        optional_split = [split] * n_splits

    if not settings.lightning:
        await wallet.mint(amount, split=optional_split)
    # user requests an invoice
    elif amount and not id:
        invoice = await wallet.request_mint(amount)
        if invoice.bolt11:
            print(f"Pay invoice to mint {amount} sat:")
            print("")
            print(f"Invoice: {invoice.bolt11}")
            print("")
            print(
                "You can use this command to check the invoice: cashu invoice"
                f" {amount} --id {invoice.id}"
            )
            if no_check:
                return
            check_until = time.time() + 5 * 60  # check for five minutes
            print("")
            print(
                "Checking invoice ...",
                end="",
                flush=True,
            )
            paid = False
            while time.time() < check_until and not paid:
                time.sleep(3)
                try:
                    await wallet.mint(amount, split=optional_split, id=invoice.id)
                    paid = True
                    print(" Invoice paid.")
                except Exception as e:
                    # TODO: user error codes!
                    if "not paid" in str(e):
                        print(".", end="", flush=True)
                        continue
                    else:
                        print(f"Error: {str(e)}")
            if not paid:
                print("\n")
                print(
                    "Invoice is not paid yet, stopping check. Use the command above to"
                    " recheck after the invoice has been paid."
                )

    # user paid invoice and want to check it
    elif amount and id:
        await wallet.mint(amount, split=optional_split, id=id)
    wallet.status()
    return


async def swap():
    if not settings.lightning:
        raise Exception("lightning not supported.")
    print("Select the mint to swap from:")
    outgoing_wallet = await get_mint_wallet(ctx, force_select=True)

    print("Select the mint to swap to:")
    incoming_wallet = await get_mint_wallet(ctx, force_select=True)

    await incoming_wallet.load_mint()
    await outgoing_wallet.load_mint()

    if incoming_wallet.url == outgoing_wallet.url:
        raise Exception("mints for swap have to be different")

    amount = int(input("Enter amount to swap in sat: "))
    assert amount > 0, "amount is not positive"

    # request invoice from incoming mint
    invoice = await incoming_wallet.request_mint(amount)

    # pay invoice from outgoing mint
    total_amount, fee_reserve_sat = await outgoing_wallet.get_pay_amount_with_fees(
        invoice.bolt11
    )
    if outgoing_wallet.available_balance < total_amount:
        raise Exception("balance too low")
    _, send_proofs = await outgoing_wallet.split_to_send(
        outgoing_wallet.proofs, total_amount, set_reserved=True
    )
    await outgoing_wallet.pay_lightning(send_proofs, invoice.bolt11, fee_reserve_sat)

    # mint token in incoming mint
    await incoming_wallet.mint(amount, id=invoice.id)

    await incoming_wallet.load_proofs(reload=True)
    await print_mint_balances(incoming_wallet, show_mints=True)


async def balance(verbose):
    wallet: Wallet = ctx.obj["WALLET"]
    await wallet.load_proofs()
    if verbose:
        # show balances per keyset
        keyset_balances = wallet.balance_per_keyset()
        if len(keyset_balances) > 1:
            print(f"You have balances in {len(keyset_balances)} keysets:")
            print("")
            for k, v in keyset_balances.items():
                print(
                    f"Keyset: {k} - Balance: {v['available']} sat (pending:"
                    f" {v['balance']-v['available']} sat)"
                )
            print("")

    await print_mint_balances(wallet)

    if verbose:
        print(
            f"Balance: {wallet.available_balance} sat (pending:"
            f" {wallet.balance-wallet.available_balance} sat) in"
            f" {len([p for p in wallet.proofs if not p.reserved])} tokens"
        )
    else:
        print(f"Balance: {wallet.available_balance} sat")


async def pending(legacy, number: int, offset: int):
    wallet: Wallet = ctx.obj["WALLET"]
    reserved_proofs = await get_reserved_proofs(wallet.db)
    if len(reserved_proofs):
        print("--------------------------\n")
        sorted_proofs = sorted(reserved_proofs, key=itemgetter("send_id"))  # type: ignore
        if number:
            number += offset
        for i, (key, value) in islice(
            enumerate(
                groupby(
                    sorted_proofs,
                    key=itemgetter("send_id"),  # type: ignore
                )
            ),
            offset,
            number,
        ):
            grouped_proofs = list(value)
            # TODO: we can't return DLEQ because we don't store it
            token = await wallet.serialize_proofs(grouped_proofs, include_dleq=False)
            tokenObj = deserialize_token_from_string(token)
            mint = [t.mint for t in tokenObj.token][0]
            # token_hidden_secret = await wallet.serialize_proofs(grouped_proofs)
            assert grouped_proofs[0].time_reserved
            reserved_date = datetime.utcfromtimestamp(
                int(grouped_proofs[0].time_reserved)
            ).strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"#{i} Amount: {sum_proofs(grouped_proofs)} sat Time:"
                f" {reserved_date} ID: {key}  Mint: {mint}\n"
            )
            print(f"{token}\n")

            if legacy:
                token_legacy = await wallet.serialize_proofs(
                    grouped_proofs,
                    legacy=True,
                )
                print(f"{token_legacy}\n")
            print("--------------------------\n")
        print("To remove all spent tokens use: cashu burn -a")


async def lock(ctx):
    wallet: Wallet = ctx.obj["WALLET"]

    pubkey = await wallet.create_p2pk_pubkey()
    lock_str = f"P2PK:{pubkey}"
    print("---- Pay to public key (P2PK) ----\n")

    print("Use a lock to receive tokens that only you can unlock.")
    print("")
    print(f"Public receiving lock: {lock_str}")
    print("")
    print(
        f"Anyone can send tokens to this lock:\n\ncashu send <amount> --lock {lock_str}"
    )
    print("")
    print("Only you can receive tokens from this lock: cashu receive <token>")


async def locks(ctx):
    wallet: Wallet = ctx.obj["WALLET"]
    # P2PK lock
    pubkey = await wallet.create_p2pk_pubkey()
    lock_str = f"P2PK:{pubkey}"
    print("---- Pay to public key (P2PK) lock ----\n")
    print(f"Lock: {lock_str}")
    print("")
    return True


async def invoices():
    wallet: Wallet = ctx.obj["WALLET"]
    invoices = await get_lightning_invoices(db=wallet.db)
    if len(invoices):
        print("")
        print("--------------------------\n")
        for invoice in invoices:
            print(f"Paid: {invoice.paid}")
            print(f"Incoming: {invoice.amount > 0}")
            print(f"Amount: {abs(invoice.amount)}")
            if invoice.id:
                print(f"ID: {invoice.id}")
            if invoice.preimage:
                print(f"Preimage: {invoice.preimage}")
            if invoice.time_created:
                d = datetime.utcfromtimestamp(
                    int(float(invoice.time_created))
                ).strftime("%Y-%m-%d %H:%M:%S")
                print(f"Created: {d}")
            if invoice.time_paid:
                d = datetime.utcfromtimestamp(int(float(invoice.time_paid))).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                print(f"Paid: {d}")
            print("")
            print(f"Payment request: {invoice.bolt11}")
            print("")
            print("--------------------------\n")
    else:
        print("No invoices found.")


async def wallets():
    # list all directories
    wallets = [
        d for d in listdir(settings.cashu_dir) if isdir(join(settings.cashu_dir, d))
    ]
    try:
        wallets.remove("mint")
    except ValueError:
        pass
    for w in wallets:
        wallet = Wallet(ctx.obj["HOST"], os.path.join(settings.cashu_dir, w))
        try:
            await wallet.load_proofs()
            if wallet.proofs and len(wallet.proofs):
                active_wallet = False
                if w == ctx.obj["WALLET_NAME"]:
                    active_wallet = True
                print(
                    f"Wallet: {w}\tBalance: {sum_proofs(wallet.proofs)} sat"
                    " (available: "
                    f"{sum_proofs([p for p in wallet.proofs if not p.reserved])} sat){' *' if active_wallet else ''}"
                )
        except Exception:
            pass


async def info(mint: bool, mnemonic: bool):
    wallet: Wallet = ctx.obj["WALLET"]
    print(f"Version: {settings.version}")
    print(f"Wallet: {ctx.obj['WALLET_NAME']}")
    if settings.debug:
        print(f"Debug: {settings.debug}")
    print(f"Cashu dir: {settings.cashu_dir}")
    if settings.env_file:
        print(f"Settings: {settings.env_file}")
    if settings.tor:
        print(f"Tor enabled: {settings.tor}")
    if settings.nostr_private_key:
        try:
            client = NostrClient(private_key=settings.nostr_private_key, connect=False)
            print(f"Nostr public key: {client.public_key.bech32()}")
            print(f"Nostr relays: {settings.nostr_relays}")
        except Exception:
            print("Nostr: Error. Invalid key.")
    if settings.socks_proxy:
        print(f"Socks proxy: {settings.socks_proxy}")
    if settings.http_proxy:
        print(f"HTTP proxy: {settings.http_proxy}")
    mint_list = await list_mints(wallet)
    print(f"Mint URLs: {mint_list}")
    if mint:
        for mint_url in mint_list:
            wallet.url = mint_url
            mint_info: dict = (await wallet._load_mint_info()).dict()
            print("")
            print("Mint information:")
            print("")
            print(f"Mint URL: {mint_url}")
            if mint_info:
                print(f"Mint name: {mint_info['name']}")
                if mint_info["description"]:
                    print(f"Description: {mint_info['description']}")
                if mint_info["description_long"]:
                    print(f"Long description: {mint_info['description_long']}")
                if mint_info["contact"]:
                    print(f"Contact: {mint_info['contact']}")
                if mint_info["version"]:
                    print(f"Version: {mint_info['version']}")
                if mint_info["motd"]:
                    print(f"Message of the day: {mint_info['motd']}")
                if mint_info["parameter"]:
                    print(f"Parameter: {mint_info['parameter']}")

    if mnemonic:
        assert wallet.mnemonic
        print(f"Mnemonic: {wallet.mnemonic}")
    return


async def restore(to: int, batch: int):
    wallet: Wallet = ctx.obj["WALLET"]
    # check if there is already a mnemonic in the database
    ret = await get_seed_and_mnemonic(wallet.db)
    if ret:
        print(
            "Wallet already has a mnemonic. You can't restore an already initialized"
            " wallet."
        )
        print("To restore a wallet, please delete the wallet directory and try again.")
        print("")
        print(
            "The wallet directory is:"
            f" {os.path.join(settings.cashu_dir, ctx.obj['WALLET_NAME'])}"
        )
        return
    # ask the user for a mnemonic but allow also no input
    print("Please enter your mnemonic to restore your balance.")
    mnemonic = input(
        "Enter mnemonic: ",
    )
    if not mnemonic:
        print("No mnemonic entered. Exiting.")
        return

    await wallet.restore_wallet_from_mnemonic(mnemonic, to=to, batch=batch)
    await wallet.load_proofs()
    wallet.status()


async def selfpay(all: bool = False):
    wallet = await get_mint_wallet(ctx, force_select=True)
    await wallet.load_mint()

    # get balance on this mint
    mint_balance_dict = await wallet.balance_per_minturl()
    mint_balance = mint_balance_dict[wallet.url]["available"]
    # send balance once to mark as reserved
    await wallet.split_to_send(wallet.proofs, mint_balance, None, set_reserved=True)
    # load all reserved proofs (including the one we just sent)
    reserved_proofs = await get_reserved_proofs(wallet.db)
    if not len(reserved_proofs):
        print("No balance on this mint.")
        return

    token = await wallet.serialize_proofs(reserved_proofs)
    print(f"Selfpay token for mint {wallet.url}:")
    print("")
    print(token)
    tokenObj = TokenV3.deserialize(token)
    await receive(wallet, tokenObj)


class MainWindow(BoxLayout):

    def show_error_popup(self, message):
        popup = Popup(
            title="Error",
            content=Button(text=message, on_press=lambda x: popup.dismiss()),
            size_hint=(None, None),
            size=(400, 200),
        )
        # Open the Popup
        popup.open()

    async def button_send_clicked(self):
        input = self.text_field.text
        try:
            amount = int(input)
        except Exception:
            self.show_error_popup("no numeric amount in input field.")
            return
        if not amount > 0:
            self.show_error_popup("amount must be greater than 0")
            return

        self.status_label.text = "Splitting tokens..."
        _, send_proofs = await wallet.split_to_send(
            wallet.proofs, amount, set_reserved=True
        )

        token = await wallet.serialize_proofs(send_proofs, include_mints=True)
        await wallet.set_reserved(send_proofs, reserved=True)
        self.text_field.text = token
        self.status_label.text = "Tokens reserved, send them to the recipient..."
        await self.update_balance()

    async def button_receive_clicked(self):
        try:
            tokenObj = deserialize_token_from_string(self.text_field.text)
            # verify that we trust all mints in these tokens
            # ask the user if they want to trust the new mints
            for mint_url in set([t.mint for t in tokenObj.token if t.mint]):
                mint_wallet = Wallet(
                    mint_url, os.path.join(settings.cashu_dir, wallet.name)
                )
                trust_mint = await verify_mint(mint_wallet, mint_url)
                if not trust_mint:
                    self.status_label.text = "Untrusted mint, aborting receive..."
                    return
            self.status_label.text = "Refreshing tokens with the mint..."
            newBalance = await receive(wallet, tokenObj)
            logger.debug(f"New balance: {newBalance}")
            await self.update_balance()
            self.status_label.text = "Receive successful..."

        except Exception as e:
            self.show_error_popup(str(e))

    async def button_pay_clicked(self):
        pass

    async def button_invoice_clicked(self):
        pass

    async def wait_to_enable_mnemonic_button(self):
        await asyncio.sleep(30)
        self.btn_mnemonic.text = "Display mnemonic (really)"
        self.btn_mnemonic.disabled = False
        self.mnemonic_button_enabled = True
        self.status_label.text = "Now you can display mnemonic..."

    async def button_display_mnemonic_clicked(self):
        if self.mnemonic_button_enabled:
            self.text_field.text = wallet.mnemonic
        else:
            self.status_label.text = (
                "Wait for 30 seconds, then press the button again..."
            )
            self.btn_mnemonic.text = "Waiting to display mnemonic..."
            self.btn_mnemonic.disabled = True
            asyncio.create_task(self.wait_to_enable_mnemonic_button())

    async def update_balance(self):
        self.balance_label.text = f"{wallet.available_balance} sat"

    async def initialize_wallet(self):
        global wallet
        self.status_label.text = "🫸 Initializing wallet...🫷"
        db_path = os.path.join(settings.cashu_dir, walletname)
        # allow to perform migrations
        wallet = await Wallet.with_db(
            settings.mint_url, db_path, name=walletname, skip_private_key=True
        )
        # load with private keys
        wallet = await Wallet.with_db(settings.mint_url, db_path, name=walletname)
        await init_wallet(wallet, load_proofs=True)
        await self.update_balance()
        self.status_label.text = "Wallet initialized, loading mint..."
        try:
            await wallet.load_mint()
        except Exception as e:
            self.status_label.text = f"Error while loading mint: {e}"
            logger.exception(e)
            raise e
        self.status_label.text = "All ready !"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.orientation = "vertical"

        # Balance Label
        self.balance_label = Label(
            text="Loading balance...", font_size="36sp", size_hint_y=None, height=40
        )
        self.add_widget(self.balance_label)

        # Status Label
        self.status_label = Label(
            text="Loading wallet...", font_size="36sp", size_hint_y=None, height=40
        )
        self.add_widget(self.status_label)

        # Text Field for input/output
        self.text_field = TextInput(height=80, multiline=True, size_hint_y=1)
        self.add_widget(self.text_field)

        # Send Button
        btn_send = Button(text="Send", size_hint_y=None, height=40)
        btn_send.bind(
            on_press=lambda x: asyncio.create_task(self.button_send_clicked())
        )
        self.add_widget(btn_send)

        # Receive Button
        btn_receive = Button(text="Receive", size_hint_y=None, height=40)
        btn_receive.bind(
            on_press=lambda x: asyncio.create_task(self.button_receive_clicked())
        )
        self.add_widget(btn_receive)

        # Pay Button
        btn_pay = Button(text="Pay (lightning invoice)", size_hint_y=None, height=40)
        btn_pay.bind(on_press=lambda x: asyncio.create_task(self.button_pay_clicked()))
        btn_pay.disabled = True
        self.add_widget(btn_pay)

        # Invoice Button
        btn_invoice = Button(
            text="Invoice (create lightning invoice)", size_hint_y=None, height=40
        )
        btn_invoice.bind(
            on_press=lambda x: asyncio.create_task(self.button_invoice_clicked())
        )
        btn_invoice.disabled = True
        self.add_widget(btn_invoice)

        # Display mnemonic
        self.mnemonic_button_enabled = False
        self.btn_mnemonic = Button(
            text="Display mnemonic seed backup", size_hint_y=None, height=40
        )
        self.btn_mnemonic.bind(
            on_press=lambda x: asyncio.create_task(
                self.button_display_mnemonic_clicked()
            )
        )
        self.add_widget(self.btn_mnemonic)

    # Placeholder for adapted button click event methods
    # These methods need to be filled with adapted logic from the original PyQt6 implementation


class NutbandApp(App):
    def build(self):
        return MainWindow()

    def on_start(self):
        task = asyncio.create_task(self.root.initialize_wallet())

        async def print_exception(task):
            await task
            if task.done() and not task.cancelled():
                exception = task.exception()
                if exception:
                    print("Exception:", exception)
                    traceback.print_tb(exception.__traceback__)

        asyncio.create_task(print_exception(task))


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    loop.run_until_complete(NutbandApp().async_run(async_lib="asyncio"))
    loop.close()
