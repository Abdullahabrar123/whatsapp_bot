# Official Meta WhatsApp Cloud API Setup Guide

This guide describes how to configure the official Meta Cloud API assets for production.

---

## 1. Meta Developer Portal Setup

1. **Create a Meta App**:
   - Go to [Meta for Developers](https://developers.facebook.com/).
   - Click **Create App** -> Select **Business** type.
   - Add the **WhatsApp** product to your app.

2. **Retrieve Key Identifiers**:
   - **Phone Number ID**: Go to **WhatsApp** -> **API Setup** -> Copy the **Phone Number ID** (e.g. `102938475610293`).
   - **WABA ID**: Copy the **WhatsApp Business Account ID** (e.g. `987654321098765`).
   - **App Secret**: Go to **App Settings** -> **Basic** -> Click **Show** under **App Secret**.

---

## 2. Generate Permanent System User Access Token

> [!WARNING]
> Do NOT use the temporary 24-hour test token in production. You must generate a permanent System User token.

1. Go to [Meta Business Manager](https://business.facebook.com/settings/).
2. Navigate to **Users** -> **System Users**.
3. Click **Add** -> Name: `whatsapp-chatbot-backend` -> Role: **Admin**.
4. Click **Add Assets** -> Select your **WhatsApp Business Account** -> Enable **Full Control** (Manage WhatsApp account).
5. Click **Generate New Token** -> Select your App -> Enable permissions:
   - `whatsapp_business_messaging`
   - `whatsapp_business_management`
6. Copy the generated permanent token and paste it into `.env` as `META_ACCESS_TOKEN`.

---

## 3. Webhook Configuration

1. In Meta Developer Portal, navigate to **WhatsApp** -> **Configuration**.
2. Click **Edit** next to Webhook:
   - **Callback URL**: `https://your-domain.com/webhooks/meta` (must be HTTPS with a valid certificate).
   - **Verify Token**: Paste the secret token you chose in `.env` for `META_WEBHOOK_VERIFY_TOKEN`.
3. Click **Verify and Save**. Meta will send a GET request to `/webhooks/meta` with `hub.challenge`.
4. Under **Webhook fields**, click **Manage** and subscribe to:
   - `messages` (inbound customer messages and outbound delivery statuses).

---

## 4. Message Templates

1. Go to **WhatsApp Manager** -> **Message Templates**.
2. Create your templates (e.g. `appointment_reminder`).
3. Once approved by Meta, register them in `config/templates.yaml`:
   ```yaml
   appointment_reminder:
     name: "appointment_reminder"
     category: "UTILITY"
     language: "en"
     approved: true
     variables: ["1", "2"]
   ```
