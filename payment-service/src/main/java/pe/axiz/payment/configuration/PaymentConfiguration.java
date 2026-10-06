package pe.axiz.payment.configuration;

import java.time.Clock;
import org.springframework.context.annotation.*;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import pe.axiz.payment.application.ports.out.*;
import pe.axiz.payment.application.service.PaymentService;

@Configuration
public class PaymentConfiguration {
  @Bean
  public TransactionTemplate transactionTemplate(PlatformTransactionManager m) {
    return new TransactionTemplate(m);
  }

  @Bean
  PaymentService service(PaymentStore s, AuthorizationPort a, MerchantPolicyPort p) {
    return new PaymentService(s, a, p, Clock.systemUTC());
  }
}
