// Premium restaurant chat ordering UI client (PRD Sections 51-60).
//
// No framework, no build step -- plain fetch() + DOM APIs. All AI/customer
// text is rendered via textContent/createTextNode, never innerHTML with
// interpolated data, so nothing in a chat message or menu item can execute
// as markup (ARCHITECTURE.md Section 9's XSS defense). The one safe
// exception is clearing a container's children, done via clearChildren()
// below rather than `el.innerHTML = ""`, so the codebase has zero innerHTML
// usage to audit.

(function () {
  "use strict";

  const SESSION_KEY = "restaurant_chat_session_id";
  const ORDER_ID_RE = /ORD-\d{8}-\d{4}/;

  function getOrCreateSessionId() {
    try {
      let sessionId = localStorage.getItem(SESSION_KEY);
      if (!sessionId) {
        sessionId = "chat_" + crypto.randomUUID().replace(/-/g, "").slice(0, 24);
        localStorage.setItem(SESSION_KEY, sessionId);
      }
      return sessionId;
    } catch (err) {
      // localStorage unavailable (private browsing, etc.) -- fall back to an
      // in-memory session id for this page load only.
      return "chat_" + crypto.randomUUID().replace(/-/g, "").slice(0, 24);
    }
  }

  const sessionId = getOrCreateSessionId();

  const log = document.getElementById("chat-log");
  const aiAvatarTemplate = document.getElementById("ai-avatar-template");
  const menuCardImageTemplate = document.getElementById("menu-card-image-template");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const sendButton = form.querySelector(".send-button");
  const typingIndicator = document.getElementById("typing-indicator");

  const cartToggle = document.getElementById("cart-toggle");
  const cartCountEl = document.getElementById("cart-count");
  const cartBackdrop = document.getElementById("cart-backdrop");
  const cartPanel = document.getElementById("cart-panel");
  const cartCloseBtn = document.getElementById("cart-close");
  const cartItemsEl = document.getElementById("cart-items");
  const cartNotesEl = document.getElementById("cart-notes");
  const cartTotalEl = document.getElementById("cart-total");

  let lastCartCount = 0;

  function clearChildren(el) {
    while (el.firstChild) {
      el.removeChild(el.firstChild);
    }
  }

  // ---------------------------------------------------------------- render

  function renderInline(parent, text) {
    const parts = text.split(/(\*\*[^*]+\*\*)/g);
    parts.forEach((part) => {
      if (!part) return;
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        const strong = document.createElement("strong");
        strong.textContent = part.slice(2, -2);
        parent.appendChild(strong);
      } else {
        parent.appendChild(document.createTextNode(part));
      }
    });
  }

  // Small, safe subset of Markdown (bold + bullet lists) rendered directly
  // to DOM nodes -- never through innerHTML/raw HTML. AI replies commonly
  // use **bold** and "- item" lists (see system_prompt.py's own examples),
  // so this reads far better than showing literal asterisks in a premium UI.
  function renderFormattedText(container, text) {
    const lines = text.split("\n");
    let currentList = null;

    lines.forEach((rawLine) => {
      const line = rawLine.trim();
      const bulletMatch = line.match(/^[-*]\s+(.*)$/);

      if (bulletMatch) {
        if (!currentList) {
          currentList = document.createElement("ul");
          container.appendChild(currentList);
        }
        const li = document.createElement("li");
        renderInline(li, bulletMatch[1]);
        currentList.appendChild(li);
        return;
      }

      currentList = null;
      if (line === "") return;

      const p = document.createElement("p");
      renderInline(p, line);
      container.appendChild(p);
    });

    if (!container.childNodes.length) {
      container.textContent = text;
    }
  }

  function appendMessage(text, role, options) {
    options = options || {};

    const row = document.createElement("div");
    row.className = "message-row " + role;

    if (role === "ai") {
      const avatar = document.createElement("span");
      avatar.className = "message-avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.appendChild(aiAvatarTemplate.content.cloneNode(true));
      row.appendChild(avatar);
    }

    const el = document.createElement("div");
    el.className = "message " + role;
    if (options.isError) el.classList.add("error");
    if (options.orderSuccess) el.classList.add("order-success");

    if (role === "ai" && !options.isError) {
      renderFormattedText(el, text);
    } else {
      el.textContent = text;
    }

    row.appendChild(el);
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  function showTyping() {
    typingIndicator.hidden = false;
    log.scrollTop = log.scrollHeight;
  }

  function hideTyping() {
    typingIndicator.hidden = true;
  }

  function setFormDisabled(disabled) {
    input.disabled = disabled;
    sendButton.disabled = disabled;
  }

  // ------------------------------------------------------------------ cart

  function cartItemCount(cart) {
    return cart.items.reduce((sum, item) => sum + item.quantity, 0);
  }

  function updateCartBadge(cart) {
    const count = cartItemCount(cart);
    if (count > 0) {
      cartCountEl.hidden = false;
      cartCountEl.textContent = String(count);
    } else {
      cartCountEl.hidden = true;
    }
    if (count > lastCartCount) {
      cartCountEl.classList.remove("bump");
      void cartCountEl.offsetWidth; // restart the animation if already applied
      cartCountEl.classList.add("bump");
    }
    lastCartCount = count;
  }

  function renderCartPanel(cart) {
    clearChildren(cartItemsEl);

    if (!cart.items.length) {
      const empty = document.createElement("div");
      empty.className = "cart-empty";
      empty.textContent = "Your cart is empty.";
      cartItemsEl.appendChild(empty);
    } else {
      cart.items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "cart-item";

        const left = document.createElement("div");

        const name = document.createElement("div");
        name.className = "cart-item-name";
        name.textContent = item.name;
        left.appendChild(name);

        const qty = document.createElement("div");
        qty.className = "cart-item-qty";
        qty.textContent = item.quantity + " × ₹" + item.price;
        left.appendChild(qty);

        if (item.instructions) {
          const instr = document.createElement("div");
          instr.className = "cart-item-instructions";
          instr.textContent = item.instructions;
          left.appendChild(instr);
        }

        const price = document.createElement("div");
        price.className = "cart-item-price";
        price.textContent = "₹" + item.price * item.quantity;

        row.appendChild(left);
        row.appendChild(price);
        cartItemsEl.appendChild(row);
      });
    }

    if (cart.order_notes) {
      cartNotesEl.hidden = false;
      cartNotesEl.textContent = "Note: " + cart.order_notes;
    } else {
      cartNotesEl.hidden = true;
      cartNotesEl.textContent = "";
    }

    cartTotalEl.textContent = "₹" + cart.total;
  }

  async function refreshCart() {
    try {
      const response = await fetch("/api/cart/" + encodeURIComponent(sessionId));
      if (!response.ok) return;
      const cart = await response.json();
      updateCartBadge(cart);
      renderCartPanel(cart);
    } catch (err) {
      // Cart sync is best-effort -- a transient failure here shouldn't block chat.
    }
  }

  function openCart() {
    cartBackdrop.hidden = false;
    cartPanel.hidden = false;
    requestAnimationFrame(() => cartPanel.classList.add("open"));
    refreshCart();
  }

  function closeCart() {
    cartPanel.classList.remove("open");
    window.setTimeout(() => {
      cartPanel.hidden = true;
      cartBackdrop.hidden = true;
    }, 220);
  }

  cartToggle.addEventListener("click", openCart);
  cartCloseBtn.addEventListener("click", closeCart);
  cartBackdrop.addEventListener("click", closeCart);

  // ------------------------------------------------------------------ menu

  function buildMenuCard(item) {
    const card = document.createElement("div");
    card.className = "menu-card";

    const image = document.createElement("div");
    image.className = "menu-card-image";
    image.setAttribute("aria-hidden", "true");
    image.appendChild(menuCardImageTemplate.content.cloneNode(true));
    card.appendChild(image);

    const name = document.createElement("div");
    name.className = "menu-card-name";

    const dot = document.createElement("span");
    dot.className = "diet-dot " + (item.is_veg ? "veg" : "nonveg");
    dot.setAttribute("aria-label", item.is_veg ? "Vegetarian" : "Non-vegetarian");
    dot.setAttribute("role", "img");
    name.appendChild(dot);

    const nameText = document.createElement("span");
    nameText.textContent = item.name;
    name.appendChild(nameText);

    card.appendChild(name);

    if (item.description) {
      const desc = document.createElement("div");
      desc.className = "menu-card-desc";
      desc.textContent = item.description;
      card.appendChild(desc);
    }

    const footer = document.createElement("div");
    footer.className = "menu-card-footer";

    const price = document.createElement("div");
    price.className = "menu-card-price";
    price.textContent = "₹" + item.price;
    footer.appendChild(price);

    const addBtn = document.createElement("button");
    addBtn.type = "button";
    addBtn.className = "add-button";
    addBtn.textContent = "Add";
    addBtn.addEventListener("click", () => addItemToCart(item.item_id, addBtn));
    footer.appendChild(addBtn);

    card.appendChild(footer);
    return card;
  }

  async function loadMenu() {
    const wrapper = document.createElement("div");
    wrapper.className = "menu-grid";
    log.appendChild(wrapper);
    log.scrollTop = log.scrollHeight;

    try {
      const response = await fetch("/api/menu");
      const items = await response.json();
      if (!response.ok || !items.length) {
        wrapper.remove();
        appendMessage("Sorry, the menu isn't available right now.", "ai", { isError: true });
        return;
      }
      items.forEach((item) => wrapper.appendChild(buildMenuCard(item)));
      log.scrollTop = log.scrollHeight;
    } catch (err) {
      wrapper.remove();
      appendMessage("I'm having trouble loading the menu right now.", "ai", { isError: true });
    }
  }

  async function addItemToCart(itemId, button) {
    button.disabled = true;
    try {
      const response = await fetch("/api/cart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, action: "add", item_id: itemId, quantity: 1 }),
      });
      const data = await response.json();

      if (!response.ok) {
        button.disabled = false;
        appendMessage(data.message || "Sorry, that item couldn't be added.", "ai", { isError: true });
        return;
      }

      button.textContent = "✓ Added";
      button.classList.add("added");
      updateCartBadge(data);
      renderCartPanel(data);
      window.setTimeout(() => {
        button.textContent = "Add";
        button.classList.remove("added");
        button.disabled = false;
      }, 1200);
    } catch (err) {
      button.disabled = false;
      appendMessage("I'm having trouble updating your cart right now.", "ai", { isError: true });
    }
  }

  document.querySelectorAll(".quick-action").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.action === "view-menu") {
        loadMenu();
      }
    });
  });

  // ------------------------------------------------------------------ chat

  async function sendMessage(message) {
    appendMessage(message, "user");
    input.value = "";
    setFormDisabled(true);
    showTyping();

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: message }),
      });
      const data = await response.json();
      hideTyping();

      if (!response.ok) {
        appendMessage(data.message || "Something went wrong. Please try again.", "ai", { isError: true });
        return;
      }

      appendMessage(data.reply, "ai", { orderSuccess: ORDER_ID_RE.test(data.reply) });
    } catch (err) {
      hideTyping();
      appendMessage("I'm having trouble connecting right now. Please try again.", "ai", { isError: true });
    } finally {
      setFormDisabled(false);
      refreshCart();
      input.focus();
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    const message = input.value.trim();
    if (message) {
      sendMessage(message);
    }
  });

  // ------------------------------------------------------------------ init

  appendMessage("Welcome! What would you like to order today?", "ai");
  refreshCart();
})();
