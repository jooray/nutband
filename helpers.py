import asyncio
from kivy.app import App
from kivy.uix.popup import Popup
from kivy.uix.button import Button
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

from cashu.wallet.crud import get_keyset
from cashu.wallet.wallet import Wallet as Wallet

async def open_yes_no_popup(title, question):
    # Create a Future object to hold the result
    future = asyncio.Future()

    # Create layout for the Popup
    layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

    # Add a Label with your message
    message_label = Label(text=question, size_hint_y=None, height=100)
    message_label.bind(size=lambda *x: setattr(message_label, 'text_size', (message_label.width, None)))
    layout.add_widget(message_label)

    # Create a horizontal layout for buttons
    button_layout = BoxLayout(orientation='horizontal', spacing=10)
    yes_button = Button(text="Yes")
    no_button = Button(text="No")

    # Bind callbacks to the buttons
    yes_button.bind(on_press=lambda x: future.set_result(True))
    no_button.bind(on_press=lambda x: future.set_result(False))

    # Add buttons to the button layout
    button_layout.add_widget(yes_button)
    button_layout.add_widget(no_button)

    # Add button layout to the main layout
    layout.add_widget(button_layout)

    # Create and open the Popup
    yes_no_popup = Popup(title=title, content=layout, size_hint=(None, None), size=(300, 200))
    yes_no_popup.open()

    # Wait for the user to click a button
    result = await future

    # Close the popup
    yes_no_popup.dismiss()

    # Return the result
    return result

async def verify_mint(mint_wallet: Wallet, url: str):
    """A helper function that asks the user if they trust the mint if the user
    has not encountered the mint before (there is no entry in the database).

    Throws an Exception if the user chooses to not trust the mint.
    """
    # dummy Wallet to check the database later
    # mint_wallet = Wallet(url, os.path.join(settings.cashu_dir, ctx.obj["WALLET_NAME"]))
    # we check the db whether we know this mint already and ask the user if not
    mint_keysets = await get_keyset(mint_url=url, db=mint_wallet.db)
    if mint_keysets is None:
        # we encountered a new mint and ask for a user confirmation
        return await open_yes_no_popup(
            "Unknown mint confirmation",
            f"Do you trust the mint \"{url}\" and want to receive the tokens?",
        )
    else:
        return True
