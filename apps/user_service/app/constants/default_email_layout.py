"""Default org email layout shell seeded on organization create."""

DEFAULT_LAYOUT_NAME = "Default layout"

DEFAULT_LAYOUT_HTML = """<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>House of Apps Email</title>
<style>
  @media only screen and (max-width: 600px) {
    .mobile-padding {
      padding: 10px !important;
    }

    .mobile-text {
      font-size: 14px !important;
    }

    .mobile-heading {
      font-size: 18px !important;
    }

    .mobile-button {
      padding: 12px 24px !important;
      font-size: 14px !important;
    }

    .mobile-social {
      width: 28px !important;
      height: 28px !important;
      line-height: 24px !important;
      font-size: 14px !important;
    }

    .social-table td {
      padding: 0 3px !important;
    }
  }
</style>
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f4f4;">
  <tbody>
    <tr>
      <td align="center" style="padding:20px 0;">
        <table width="" cellpadding="0" cellspacing="0" border="0"
          style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,0.1);">
          <tbody>
            <tr>
              <td style="padding:30px 40px 20px 40px;text-align:left;" class="mobile-padding">
                <table width="100%" cellpadding="0" cellspacing="0" border="0">
                  <tbody>
                    <tr>
                      <td>
                        <img
                          src="https://b2b.newkommerce.com/_next/image?url=https%3A%2F%2Fcdn.qykly.com%2Fqykly%2Fuser%2FkJ8ZQ-_GGZ&w=256&q=75&dpl=dpl_8S4mm1QqYtDWWWE4SXH3inc7vzjm"
                          alt="House of Apps" style="height:30px;" />
                      </td>
                    </tr>
                  </tbody>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:0 40px 0px 40px;" class="mobile-padding" id="body-inject">{{BODY_CONTENT}}</td>
            </tr>
            <tr>
              <td style="padding:0px 40px 30px;background-color:#ffffff;" class="mobile-padding">
                <div style="width:100%;height:1px;background-color:#eee;margin:20px 0;"></div>
                <table width="100%" cellpadding="0" cellspacing="0" border="0">
                  <tbody>
                    <tr>
                      <td style="font-size:14px;color:#000000;font-family:Arial,sans-serif;line-height:1.4;">Cheers!
                        Team House of Apps</td>
                      <td align="right" style="padding-left:20px;">
                        <table cellpadding="0" cellspacing="0" border="0" class="social-table">
                          <tbody>
                            <tr>
                              <td style="padding:0 4px;">
                                <a href="https://www.tiktok.com/@appscrip" target="_blank">
                                  <img src="https://cdn.qykly.com/qykly/user/1LgpKp__qT" alt="TikTok"
                                    style="width:32px;height:32px;" class="mobile-social" />
                                </a>
                              </td>
                              <td style="padding:0 4px;">
                                <a href="https://www.instagram.com/appscrip" target="_blank">
                                  <img src="https://cdn.qykly.com/qykly/user/V-ni32fKdG" alt="Instagram"
                                    style="width:32px;height:32px;" class="mobile-social" />
                                </a>
                              </td>
                              <td style="padding:0 4px;">
                                <a href="https://in.linkedin.com/company/appscrip" target="_blank">
                                  <img src="https://cdn.qykly.com/qykly/user/LFiAO-b3g0" alt="LinkedIn"
                                    style="width:32px;height:32px;" class="mobile-social" />
                                </a>
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div style="width:100%;height:1px;background-color:#eee;margin:20px 0;"></div>
                <p
                  style="text-align:center;font-size:12px;color:#666;margin:16px 0 0 0;line-height:1.4;font-family:Arial,sans-serif;">
                  Please do not reply to this e-mail. If you would like to contact the House of Apps team, please
                  contact us using one of the following options:</p>
                <div style="margin-top:10px;text-align:center;">
                  <table cellpadding="0" cellspacing="0" border="0" align="center">
                    <tbody>
                      <tr>
                        <td style="font-size:14px;color:#000;font-family:Arial,sans-serif;line-height:1.5;">
                          <img src="https://cdn.qykly.com/qykly/user/SrpAT9e_PH" width="16" height="16"
                            style="vertical-align:middle;margin-right:6px;" />
                          <a href="mailto:rahul@houseofapps.ai"
                            style="color:#000;text-decoration:underline;margin-right:20px;">rahul@houseofapps.ai</a>
                          <img src="https://cdn.qykly.com/qykly/user/E6-4dJMcBV" width="16" height="16"
                            style="vertical-align:middle;margin-right:6px;" />
                          <a href="tel:+14158135833" style="color:#000;text-decoration:underline;margin-right:12px;">+1
                            415
                            813 5833</a>
                          <a href="tel:+919902019342" style="color:#000;text-decoration:underline;">+91 990 201 9342</a>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </td>
    </tr>
  </tbody>
</table>"""
