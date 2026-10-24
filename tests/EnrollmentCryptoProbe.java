import org.eegmovement.relay.EnrollmentBridge;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.spec.MGF1ParameterSpec;
import java.util.Arrays;
import javax.crypto.*;
import javax.crypto.spec.*;

/** Cross-provider enrollment envelope checks. Contains generated test keys only. */
public final class EnrollmentCryptoProbe {
    static void require(boolean value,String name){if(!value)throw new AssertionError(name);}
    public static void main(String[] args)throws Exception {
        KeyPairGenerator generator=KeyPairGenerator.getInstance("RSA");generator.initialize(3072);KeyPair pair=generator.generateKeyPair();
        byte[] key=new byte[32],nonce=new byte[12];new SecureRandom().nextBytes(key);new SecureRandom().nextBytes(nonce);
        String request="d6a64377d51340b2a61d745ad8381fa7";byte[] plain="{\"mode\":\"relay\",\"test_only\":true}".getBytes(StandardCharsets.UTF_8);
        Cipher rsa=Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");rsa.init(Cipher.ENCRYPT_MODE,pair.getPublic(),new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));
        byte[] wrapped=rsa.doFinal(key);Cipher aes=Cipher.getInstance("AES/GCM/NoPadding");aes.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(key,"AES"),new GCMParameterSpec(128,nonce));aes.updateAAD(request.getBytes(StandardCharsets.UTF_8));byte[] ciphertext=aes.doFinal(plain);
        require(Arrays.equals(plain,EnrollmentBridge.openSealed(pair.getPrivate(),request,wrapped,nonce,ciphertext)),"envelope roundtrip");
        boolean rejected=false;try{EnrollmentBridge.openSealed(pair.getPrivate(),request+"x",wrapped,nonce,ciphertext);}catch(GeneralSecurityException e){rejected=true;}require(rejected,"wrong request AAD rejected");
        ciphertext[0]^=1;rejected=false;try{EnrollmentBridge.openSealed(pair.getPrivate(),request,wrapped,nonce,ciphertext);}catch(GeneralSecurityException e){rejected=true;}require(rejected,"tampered ciphertext rejected");
        System.out.println("enrollment crypto: roundtrip, request binding, ciphertext integrity passed");
    }
}
