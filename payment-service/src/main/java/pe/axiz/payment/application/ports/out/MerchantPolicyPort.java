package pe.axiz.payment.application.ports.out;

import java.math.BigDecimal;

public interface MerchantPolicyPort {
  BigDecimal limit(String merchantId);
}
